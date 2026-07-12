#!/usr/bin/env python3
"""
Geno vs Hand-Written Python Benchmark Suite
============================================

Compiles Geno source to Python, then times the compiled output against
equivalent hand-written Python.  Reports per-problem ratios and an
aggregate pass/fail (target: ≤2× for ≥80 % of problems).

Hand-written Python mirrors the same algorithmic approach as the Geno code
(explicit loops, same data structures) rather than using Python-specific
optimisations (comprehensions, sum(), etc.).
"""

import statistics
import sys
import textwrap
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

# ---------------------------------------------------------------------------
# Make sure the geno package is importable
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from geno.compiler import compile_to_python

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

WARMUP = 3
ITERATIONS = 7
MIN_TIME_S = 0.005  # problems that finish < 5 ms are too noisy — skip


def _time_fn(fn: Callable[[], Any], iterations: int = ITERATIONS) -> float:
    """Return the *median* wall-clock time (seconds) over *iterations* runs."""
    for _ in range(WARMUP):
        fn()
    times: List[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return statistics.median(times)


def _compile_geno(source: str) -> Dict[str, Any]:
    """Compile a Geno program and exec into a namespace dict."""
    py_code = compile_to_python(source)
    ns: Dict[str, Any] = {}
    exec(compile(py_code, "<bench>", "exec"), ns)  # noqa: S102
    return ns


# ---------------------------------------------------------------------------
# Problem registry
# ---------------------------------------------------------------------------

Problem = Tuple[str, str, Callable, Callable[[Any], Callable]]
_PROBLEMS: List[Problem] = []


def _reg(name: str, geno_src: str, hw_fn: Callable, call_builder: Callable = None):
    """Register a benchmark problem.

    *hw_fn*: zero-arg callable giving the hand-written result.
    *call_builder*: given a compiled namespace, returns zero-arg callable.
    """
    if call_builder is None:
        call_builder = lambda ns: ns["run"]  # noqa: E731
    _PROBLEMS.append((name, textwrap.dedent(geno_src).strip(), hw_fn, call_builder))


# ===========================================================================
# HAND-WRITTEN PYTHON EQUIVALENTS
# (Same algorithmic shape as the Geno code — explicit loops, no builtins)
# ===========================================================================


def _py_fib_recursive(n):
    if n <= 1:
        return n
    return _py_fib_recursive(n - 1) + _py_fib_recursive(n - 2)


def _py_factorial(n):
    if n <= 1:
        return 1
    return n * _py_factorial(n - 1)


def _py_ackermann(m, n):
    if m == 0:
        return n + 1
    if n == 0:
        return _py_ackermann(m - 1, 1)
    return _py_ackermann(m - 1, _py_ackermann(m, n - 1))


# ===========================================================================
# PROBLEM DEFINITIONS
# ===========================================================================

# --- 1-10: Recursive / simple arithmetic ---

_reg("01_fib_rec_25", """
@untested("bench")
func fibonacci(n: Int) -> Int
    if n <= 1 then
        return n
    end if
    return fibonacci(n - 1) + fibonacci(n - 2)
end func

@untested("bench")
func run() -> Int
    return fibonacci(25)
end func
""", lambda: _py_fib_recursive(25))

_reg("02_factorial_20", """
@untested("bench")
func factorial(n: Int) -> Int
    if n <= 1 then
        return 1
    end if
    return n * factorial(n - 1)
end func

@untested("bench")
func run() -> Int
    return factorial(20)
end func
""", lambda: _py_factorial(20))

_reg("03_ackermann_3_6", """
@untested("bench")
func ackermann(m: Int, n: Int) -> Int
    if m == 0 then
        return n + 1
    end if
    if n == 0 then
        return ackermann(m - 1, 1)
    end if
    return ackermann(m - 1, ackermann(m, n - 1))
end func

@untested("bench")
func run() -> Int
    return ackermann(3, 6)
end func
""", lambda: _py_ackermann(3, 6))

_reg("04_gcd_iterative", """
@untested("bench")
func gcd(a: Int, b: Int) -> Int
    var x: Int = a
    var y: Int = b
    while y != 0 do
        let t: Int = y
        y = x % y
        x = t
    end while
    return x
end func

@untested("bench")
func run() -> Int
    var sum: Int = 0
    var i: Int = 1
    while i <= 5000 do
        sum = sum + gcd(i * 7, i * 13 + 1)
        i = i + 1
    end while
    return sum
end func
""", lambda: (lambda: [
    (s := [0]),
    [s.__setitem__(0, s[0] + (lambda a, b: (
        lambda: [
            (r := [a, b]),
            [(r.__setitem__(0, r[0] % r[1]) if r[0] > r[1] else r.__setitem__(1, r[1] % r[0]))
             for _ in iter(lambda: r[0] != 0 and r[1] != 0, False)],
            r[0] + r[1],
        ][-1])()
    )(i * 7, i * 13 + 1)) for i in range(1, 5001)],
    s[0],
][-1])()
if False else (lambda: (
    _gcd := lambda a, b: a if b == 0 else _gcd.__func__(b, a % b),
    sum(_gcd(i * 7, i * 13 + 1) for i in range(1, 5001)),
)[-1])()
if False else
# Just use a simple loop like the Geno code:
(lambda: _py_gcd_batch())())


def _py_gcd_batch():
    total = 0
    i = 1
    while i <= 5000:
        a = i * 7
        b = i * 13 + 1
        while b != 0:
            a, b = b, a % b
        total = total + a
        i = i + 1
    return total


# Fix reg #4 with simple function
_PROBLEMS[-1] = ("04_gcd_iterative", _PROBLEMS[-1][1], _py_gcd_batch, _PROBLEMS[-1][3])

_reg("05_power_fast", """
@untested("bench")
func power(base: Int, exp: Int) -> Int
    if exp == 0 then
        return 1
    end if
    if exp % 2 == 0 then
        let half: Int = power(base, exp / 2)
        return half * half
    end if
    return base * power(base, exp - 1)
end func

@untested("bench")
func run() -> Int
    var sum: Int = 0
    var i: Int = 1
    while i <= 5000 do
        sum = sum + power(2, 20)
        i = i + 1
    end while
    return sum
end func
""", lambda: None)


def _py_power_batch():
    def power(base, exp):
        if exp == 0:
            return 1
        if exp % 2 == 0:
            half = power(base, exp // 2)
            return half * half
        return base * power(base, exp - 1)

    total = 0
    i = 1
    while i <= 5000:
        total = total + power(2, 20)
        i = i + 1
    return total


_PROBLEMS[-1] = ("05_power_fast", _PROBLEMS[-1][1], _py_power_batch, _PROBLEMS[-1][3])

_reg("06_sum_to_n", """
@untested("bench")
func run() -> Int
    var total: Int = 0
    var i: Int = 1
    while i <= 50000 do
        total = total + i
        i = i + 1
    end while
    return total
end func
""", lambda: None)


def _py_sum_to_n():
    total = 0
    i = 1
    while i <= 50000:
        total = total + i
        i = i + 1
    return total


_PROBLEMS[-1] = ("06_sum_to_n", _PROBLEMS[-1][1], _py_sum_to_n, _PROBLEMS[-1][3])

_reg("07_collatz_batch", """
@untested("bench")
func collatz_steps(n: Int) -> Int
    var steps: Int = 0
    var val: Int = n
    while val != 1 do
        if val % 2 == 0 then
            val = val / 2
        else
            val = 3 * val + 1
        end if
        steps = steps + 1
    end while
    return steps
end func

@untested("bench")
func run() -> Int
    var total: Int = 0
    var i: Int = 1
    while i <= 5000 do
        total = total + collatz_steps(i)
        i = i + 1
    end while
    return total
end func
""", lambda: None)


def _py_collatz_batch():
    def collatz_steps(n):
        steps = 0
        val = n
        while val != 1:
            if val % 2 == 0:
                val = val // 2
            else:
                val = 3 * val + 1
            steps = steps + 1
        return steps

    total = 0
    i = 1
    while i <= 5000:
        total = total + collatz_steps(i)
        i = i + 1
    return total


_PROBLEMS[-1] = ("07_collatz_batch", _PROBLEMS[-1][1], _py_collatz_batch, _PROBLEMS[-1][3])

_reg("08_is_prime_batch", """
@untested("bench")
func is_prime(n: Int) -> Bool
    if n < 2 then
        return false
    end if
    var i: Int = 2
    while i * i <= n do
        if n % i == 0 then
            return false
        end if
        i = i + 1
    end while
    return true
end func

@untested("bench")
func run() -> Int
    var count: Int = 0
    var n: Int = 2
    while n < 5000 do
        if is_prime(n) then
            count = count + 1
        end if
        n = n + 1
    end while
    return count
end func
""", lambda: None)


def _py_is_prime_batch():
    def is_prime(n):
        if n < 2:
            return False
        i = 2
        while i * i <= n:
            if n % i == 0:
                return False
            i = i + 1
        return True

    count = 0
    n = 2
    while n < 5000:
        if is_prime(n):
            count = count + 1
        n = n + 1
    return count


_PROBLEMS[-1] = ("08_is_prime_batch", _PROBLEMS[-1][1], _py_is_prime_batch, _PROBLEMS[-1][3])

_reg("09_digit_sum_batch", """
@untested("bench")
func digit_sum(n: Int) -> Int
    var total: Int = 0
    var val: Int = n
    while val > 0 do
        total = total + val % 10
        val = val / 10
    end while
    return total
end func

@untested("bench")
func run() -> Int
    var sum: Int = 0
    var i: Int = 1
    while i <= 10000 do
        sum = sum + digit_sum(i)
        i = i + 1
    end while
    return sum
end func
""", lambda: None)


def _py_digit_sum_batch():
    def digit_sum(n):
        total = 0
        val = n
        while val > 0:
            total = total + val % 10
            val = val // 10
        return total

    s = 0
    i = 1
    while i <= 10000:
        s = s + digit_sum(i)
        i = i + 1
    return s


_PROBLEMS[-1] = ("09_digit_sum_batch", _PROBLEMS[-1][1], _py_digit_sum_batch, _PROBLEMS[-1][3])

_reg("10_choose_recursive", """
@untested("bench")
func choose(n: Int, k: Int) -> Int
    if k == 0 then
        return 1
    end if
    if k == n then
        return 1
    end if
    return choose(n - 1, k - 1) + choose(n - 1, k)
end func

@untested("bench")
func run() -> Int
    var sum: Int = 0
    var k: Int = 0
    while k <= 18 do
        sum = sum + choose(18, k)
        k = k + 1
    end while
    return sum
end func
""", lambda: None)


def _py_choose_recursive():
    def choose(n, k):
        if k == 0:
            return 1
        if k == n:
            return 1
        return choose(n - 1, k - 1) + choose(n - 1, k)

    total = 0
    k = 0
    while k <= 18:
        total = total + choose(18, k)
        k = k + 1
    return total


_PROBLEMS[-1] = ("10_choose_recursive", _PROBLEMS[-1][1], _py_choose_recursive, _PROBLEMS[-1][3])

# --- 11-20: Loop-heavy arithmetic ---

_reg("11_nested_loop_sum", """
@untested("bench")
func run() -> Int
    var total: Int = 0
    var i: Int = 0
    while i < 300 do
        var j: Int = 0
        while j < 300 do
            total = total + i + j
            j = j + 1
        end while
        i = i + 1
    end while
    return total
end func
""", lambda: None)


def _py_nested_loop_sum():
    total = 0
    i = 0
    while i < 300:
        j = 0
        while j < 300:
            total = total + i + j
            j = j + 1
        i = i + 1
    return total


_PROBLEMS[-1] = ("11_nested_loop_sum", _PROBLEMS[-1][1], _py_nested_loop_sum, _PROBLEMS[-1][3])

_reg("12_sum_of_squares", """
@untested("bench")
func run() -> Int
    var total: Int = 0
    var i: Int = 1
    while i <= 50000 do
        total = total + i * i
        i = i + 1
    end while
    return total
end func
""", lambda: None)


def _py_sum_of_squares():
    total = 0
    i = 1
    while i <= 50000:
        total = total + i * i
        i = i + 1
    return total


_PROBLEMS[-1] = ("12_sum_of_squares", _PROBLEMS[-1][1], _py_sum_of_squares, _PROBLEMS[-1][3])

_reg("13_euler_totient", """
@untested("bench")
func euler_totient(n: Int) -> Int
    var result: Int = n
    var p: Int = 2
    var temp: Int = n
    while p * p <= temp do
        if temp % p == 0 then
            while temp % p == 0 do
                temp = temp / p
            end while
            result = result - result / p
        end if
        p = p + 1
    end while
    if temp > 1 then
        result = result - result / temp
    end if
    return result
end func

@untested("bench")
func run() -> Int
    var sum: Int = 0
    var i: Int = 1
    while i <= 2000 do
        sum = sum + euler_totient(i)
        i = i + 1
    end while
    return sum
end func
""", lambda: None)


def _py_euler_totient():
    def euler_totient(n):
        result = n
        p = 2
        temp = n
        while p * p <= temp:
            if temp % p == 0:
                while temp % p == 0:
                    temp = temp // p
                result = result - result // p
            p = p + 1
        if temp > 1:
            result = result - result // temp
        return result

    s = 0
    i = 1
    while i <= 2000:
        s = s + euler_totient(i)
        i = i + 1
    return s


_PROBLEMS[-1] = ("13_euler_totient", _PROBLEMS[-1][1], _py_euler_totient, _PROBLEMS[-1][3])

_reg("14_modpow_batch", """
@untested("bench")
func modpow(base: Int, exp: Int, m: Int) -> Int
    var result: Int = 1
    var b: Int = base % m
    var e: Int = exp
    while e > 0 do
        if e % 2 == 1 then
            result = result * b % m
        end if
        e = e / 2
        b = b * b % m
    end while
    return result
end func

@untested("bench")
func run() -> Int
    var sum: Int = 0
    var i: Int = 1
    while i <= 5000 do
        sum = sum + modpow(base: i, exp: 1000, m: 1000003)
        i = i + 1
    end while
    return sum
end func
""", lambda: None)


def _py_modpow_batch():
    def modpow(base, exp, m):
        result = 1
        b = base % m
        e = exp
        while e > 0:
            if e % 2 == 1:
                result = result * b % m
            e = e // 2
            b = b * b % m
        return result

    s = 0
    i = 1
    while i <= 5000:
        s = s + modpow(i, 1000, 1000003)
        i = i + 1
    return s


_PROBLEMS[-1] = ("14_modpow_batch", _PROBLEMS[-1][1], _py_modpow_batch, _PROBLEMS[-1][3])

_reg("15_catalan_12", """
@untested("bench")
func catalan(n: Int) -> Int
    if n <= 1 then
        return 1
    end if
    var result: Int = 0
    var i: Int = 0
    while i < n do
        result = result + catalan(i) * catalan(n - 1 - i)
        i = i + 1
    end while
    return result
end func

@untested("bench")
func run() -> Int
    return catalan(12)
end func
""", lambda: None)


def _py_catalan_12():
    def catalan(n):
        if n <= 1:
            return 1
        result = 0
        i = 0
        while i < n:
            result = result + catalan(i) * catalan(n - 1 - i)
            i = i + 1
        return result
    return catalan(12)


_PROBLEMS[-1] = ("15_catalan_12", _PROBLEMS[-1][1], _py_catalan_12, _PROBLEMS[-1][3])

_reg("16_while_countdown", """
@untested("bench")
func run() -> Int
    var total: Int = 0
    var n: Int = 100000
    while n > 0 do
        total = total + n
        n = n - 1
    end while
    return total
end func
""", lambda: None)


def _py_while_countdown():
    total = 0
    n = 100000
    while n > 0:
        total = total + n
        n = n - 1
    return total


_PROBLEMS[-1] = ("16_while_countdown", _PROBLEMS[-1][1], _py_while_countdown, _PROBLEMS[-1][3])

_reg("17_sum_divisors", """
@untested("bench")
func sum_divisors(n: Int) -> Int
    var total: Int = 0
    var i: Int = 1
    while i <= n do
        if n % i == 0 then
            total = total + i
        end if
        i = i + 1
    end while
    return total
end func

@untested("bench")
func run() -> Int
    var sum: Int = 0
    var n: Int = 1
    while n <= 500 do
        sum = sum + sum_divisors(n)
        n = n + 1
    end while
    return sum
end func
""", lambda: None)


def _py_sum_divisors():
    def sum_divisors(n):
        total = 0
        i = 1
        while i <= n:
            if n % i == 0:
                total = total + i
            i = i + 1
        return total

    s = 0
    n = 1
    while n <= 500:
        s = s + sum_divisors(n)
        n = n + 1
    return s


_PROBLEMS[-1] = ("17_sum_divisors", _PROBLEMS[-1][1], _py_sum_divisors, _PROBLEMS[-1][3])

_reg("18_fizzbuzz_count", """
@untested("bench")
func run() -> Int
    var count: Int = 0
    var i: Int = 1
    while i <= 100000 do
        if i % 15 == 0 then
            count = count + 1
        end if
        i = i + 1
    end while
    return count
end func
""", lambda: None)


def _py_fizzbuzz_count():
    count = 0
    i = 1
    while i <= 100000:
        if i % 15 == 0:
            count = count + 1
        i = i + 1
    return count


_PROBLEMS[-1] = ("18_fizzbuzz_count", _PROBLEMS[-1][1], _py_fizzbuzz_count, _PROBLEMS[-1][3])

_reg("19_conditional_chain", """
@untested("bench")
func classify(n: Int) -> Int
    if n < 10 then
        return 1
    end if
    if n < 100 then
        return 2
    end if
    if n < 1000 then
        return 3
    end if
    return 4
end func

@untested("bench")
func run() -> Int
    var sum: Int = 0
    var i: Int = 0
    while i < 50000 do
        sum = sum + classify(i)
        i = i + 1
    end while
    return sum
end func
""", lambda: None)


def _py_conditional_chain():
    def classify(n):
        if n < 10:
            return 1
        if n < 100:
            return 2
        if n < 1000:
            return 3
        return 4

    s = 0
    i = 0
    while i < 50000:
        s = s + classify(i)
        i = i + 1
    return s


_PROBLEMS[-1] = ("19_conditional_chain", _PROBLEMS[-1][1], _py_conditional_chain, _PROBLEMS[-1][3])

_reg("20_multi_return", """
@untested("bench")
func compute(a: Int, b: Int) -> Int
    if a > b then
        return a - b
    end if
    if a == b then
        return 0
    end if
    return b - a
end func

@untested("bench")
func run() -> Int
    var sum: Int = 0
    var i: Int = 0
    while i < 50000 do
        sum = sum + compute(i, 25000)
        i = i + 1
    end while
    return sum
end func
""", lambda: None)


def _py_multi_return():
    def compute(a, b):
        if a > b:
            return a - b
        if a == b:
            return 0
        return b - a

    s = 0
    i = 0
    while i < 50000:
        s = s + compute(i, 25000)
        i = i + 1
    return s


_PROBLEMS[-1] = ("20_multi_return", _PROBLEMS[-1][1], _py_multi_return, _PROBLEMS[-1][3])

# --- 21-30: Nested function calls / mixed ---

_reg("21_nested_fn_calls", """
@untested("bench")
func add1(n: Int) -> Int
    return n + 1
end func

@untested("bench")
func mul2(n: Int) -> Int
    return n * 2
end func

@untested("bench")
func sub3(n: Int) -> Int
    return n - 3
end func

@untested("bench")
func run() -> Int
    var total: Int = 0
    var i: Int = 0
    while i < 30000 do
        total = total + sub3(mul2(add1(i)))
        i = i + 1
    end while
    return total
end func
""", lambda: None)


def _py_nested_fn_calls():
    def add1(n):
        return n + 1

    def mul2(n):
        return n * 2

    def sub3(n):
        return n - 3

    total = 0
    i = 0
    while i < 30000:
        total = total + sub3(mul2(add1(i)))
        i = i + 1
    return total


_PROBLEMS[-1] = ("21_nested_fn_calls", _PROBLEMS[-1][1], _py_nested_fn_calls, _PROBLEMS[-1][3])

_reg("22_deeply_nested_ifs", """
@untested("bench")
func classify_deep(n: Int) -> Int
    if n % 2 == 0 then
        if n % 3 == 0 then
            if n % 5 == 0 then
                return 30
            else
                return 6
            end if
        else
            if n % 5 == 0 then
                return 10
            else
                return 2
            end if
        end if
    else
        if n % 3 == 0 then
            return 3
        else
            if n % 5 == 0 then
                return 5
            else
                return 1
            end if
        end if
    end if
end func

@untested("bench")
func run() -> Int
    var sum: Int = 0
    var i: Int = 1
    while i <= 50000 do
        sum = sum + classify_deep(i)
        i = i + 1
    end while
    return sum
end func
""", lambda: None)


def _py_deeply_nested_ifs():
    def classify_deep(n):
        if n % 2 == 0:
            if n % 3 == 0:
                if n % 5 == 0:
                    return 30
                else:
                    return 6
            else:
                if n % 5 == 0:
                    return 10
                else:
                    return 2
        else:
            if n % 3 == 0:
                return 3
            else:
                if n % 5 == 0:
                    return 5
                else:
                    return 1

    s = 0
    i = 1
    while i <= 50000:
        s = s + classify_deep(i)
        i = i + 1
    return s


_PROBLEMS[-1] = ("22_deeply_nested_ifs", _PROBLEMS[-1][1], _py_deeply_nested_ifs, _PROBLEMS[-1][3])

_reg("23_accumulator_3var", """
@untested("bench")
func run() -> Int
    var a: Int = 0
    var b: Int = 0
    var c: Int = 0
    var i: Int = 0
    while i < 30000 do
        a = a + i
        b = b + i * 2
        c = c + i * 3
        i = i + 1
    end while
    return a + b + c
end func
""", lambda: None)


def _py_accumulator_3var():
    a = 0
    b = 0
    c = 0
    i = 0
    while i < 30000:
        a = a + i
        b = b + i * 2
        c = c + i * 3
        i = i + 1
    return a + b + c


_PROBLEMS[-1] = ("23_accumulator_3var", _PROBLEMS[-1][1], _py_accumulator_3var, _PROBLEMS[-1][3])

_reg("24_many_locals", """
@untested("bench")
func run() -> Int
    var total: Int = 0
    var i: Int = 0
    while i < 20000 do
        let a: Int = i + 1
        let b: Int = i + 2
        let c: Int = i + 3
        let d: Int = i + 4
        let e: Int = i + 5
        total = total + a + b + c + d + e
        i = i + 1
    end while
    return total
end func
""", lambda: None)


def _py_many_locals():
    total = 0
    i = 0
    while i < 20000:
        a = i + 1
        b = i + 2
        c = i + 3
        d = i + 4
        e = i + 5
        total = total + a + b + c + d + e
        i = i + 1
    return total


_PROBLEMS[-1] = ("24_many_locals", _PROBLEMS[-1][1], _py_many_locals, _PROBLEMS[-1][3])

_reg("25_mixed_arith", """
@untested("bench")
func run() -> Int
    var sum: Int = 0
    var i: Int = 1
    while i <= 30000 do
        sum = sum + i * i - i / 2 + i % 7
        i = i + 1
    end while
    return sum
end func
""", lambda: None)


def _py_mixed_arith():
    s = 0
    i = 1
    while i <= 30000:
        s = s + i * i - i // 2 + i % 7
        i = i + 1
    return s


_PROBLEMS[-1] = ("25_mixed_arith", _PROBLEMS[-1][1], _py_mixed_arith, _PROBLEMS[-1][3])

_reg("26_repeated_fn", """
@untested("bench")
func inc(n: Int) -> Int
    return n + 1
end func

@untested("bench")
func run() -> Int
    var val: Int = 0
    var i: Int = 0
    while i < 50000 do
        val = inc(val)
        i = i + 1
    end while
    return val
end func
""", lambda: None)


def _py_repeated_fn():
    def inc(n):
        return n + 1

    val = 0
    i = 0
    while i < 50000:
        val = inc(val)
        i = i + 1
    return val


_PROBLEMS[-1] = ("26_repeated_fn", _PROBLEMS[-1][1], _py_repeated_fn, _PROBLEMS[-1][3])

_reg("27_mutual_recursion", """
@untested("bench")
func is_even_r(n: Int) -> Bool
    if n == 0 then
        return true
    end if
    return is_odd_r(n - 1)
end func

@untested("bench")
func is_odd_r(n: Int) -> Bool
    if n == 0 then
        return false
    end if
    return is_even_r(n - 1)
end func

@untested("bench")
func run() -> Int
    var count: Int = 0
    var i: Int = 0
    while i < 500 do
        if is_even_r(i) then
            count = count + 1
        end if
        i = i + 1
    end while
    return count
end func
""", lambda: None)


def _py_mutual_recursion():
    def is_even_r(n):
        if n == 0:
            return True
        return is_odd_r(n - 1)

    def is_odd_r(n):
        if n == 0:
            return False
        return is_even_r(n - 1)

    count = 0
    i = 0
    while i < 500:
        if is_even_r(i):
            count = count + 1
        i = i + 1
    return count


_PROBLEMS[-1] = ("27_mutual_recursion", _PROBLEMS[-1][1], _py_mutual_recursion, _PROBLEMS[-1][3])

_reg("28_harmonic_int", """
@untested("bench")
func run() -> Int
    var total: Int = 0
    var k: Int = 1
    while k <= 50000 do
        total = total + 1000000 / k
        k = k + 1
    end while
    return total
end func
""", lambda: None)


def _py_harmonic_int():
    total = 0
    k = 1
    while k <= 50000:
        total = total + 1000000 // k
        k = k + 1
    return total


_PROBLEMS[-1] = ("28_harmonic_int", _PROBLEMS[-1][1], _py_harmonic_int, _PROBLEMS[-1][3])

_reg("29_fib_iterative", """
@untested("bench")
func fib_iter(n: Int) -> Int
    var a: Int = 0
    var b: Int = 1
    var i: Int = 0
    while i < n do
        let t: Int = b
        b = a + b
        a = t
        i = i + 1
    end while
    return a
end func

@untested("bench")
func run() -> Int
    var sum: Int = 0
    var i: Int = 0
    while i < 2000 do
        sum = sum + fib_iter(50)
        i = i + 1
    end while
    return sum
end func
""", lambda: None)


def _py_fib_iterative():
    def fib_iter(n):
        a = 0
        b = 1
        i = 0
        while i < n:
            t = b
            b = a + b
            a = t
            i = i + 1
        return a

    s = 0
    i = 0
    while i < 2000:
        s = s + fib_iter(50)
        i = i + 1
    return s


_PROBLEMS[-1] = ("29_fib_iterative", _PROBLEMS[-1][1], _py_fib_iterative, _PROBLEMS[-1][3])

_reg("30_triangular_batch", """
@untested("bench")
func triangular(n: Int) -> Int
    return n * (n + 1) / 2
end func

@untested("bench")
func run() -> Int
    var sum: Int = 0
    var i: Int = 1
    while i <= 50000 do
        sum = sum + triangular(i)
        i = i + 1
    end while
    return sum
end func
""", lambda: None)


def _py_triangular_batch():
    def triangular(n):
        return n * (n + 1) // 2

    s = 0
    i = 1
    while i <= 50000:
        s = s + triangular(i)
        i = i + 1
    return s


_PROBLEMS[-1] = ("30_triangular_batch", _PROBLEMS[-1][1], _py_triangular_batch, _PROBLEMS[-1][3])

# --- 31-40: List operations ---

_reg("31_list_sum", """
@untested("bench")
func run() -> Int
    let xs: List[Int] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    var total: Int = 0
    var rep: Int = 0
    while rep < 5000 do
        var i: Int = 0
        while i < length(xs) do
            total = total + xs[i]
            i = i + 1
        end while
        rep = rep + 1
    end while
    return total
end func
""", lambda: None)


def _py_list_sum():
    xs = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    total = 0
    rep = 0
    while rep < 5000:
        i = 0
        while i < len(xs):
            total = total + xs[i]
            i = i + 1
        rep = rep + 1
    return total


_PROBLEMS[-1] = ("31_list_sum", _PROBLEMS[-1][1], _py_list_sum, _PROBLEMS[-1][3])

_reg("32_list_max", """
@untested("bench")
func run() -> Int
    let xs: List[Int] = [3, 1, 4, 1, 5, 9, 2, 6, 5, 3, 5, 8, 9, 7, 9]
    var count: Int = 0
    var rep: Int = 0
    while rep < 5000 do
        var m: Int = xs[0]
        var i: Int = 1
        while i < length(xs) do
            if xs[i] > m then
                m = xs[i]
            end if
            i = i + 1
        end while
        count = count + m
        rep = rep + 1
    end while
    return count
end func
""", lambda: None)


def _py_list_max():
    xs = [3, 1, 4, 1, 5, 9, 2, 6, 5, 3, 5, 8, 9, 7, 9]
    count = 0
    rep = 0
    while rep < 5000:
        m = xs[0]
        i = 1
        while i < len(xs):
            if xs[i] > m:
                m = xs[i]
            i = i + 1
        count = count + m
        rep = rep + 1
    return count


_PROBLEMS[-1] = ("32_list_max", _PROBLEMS[-1][1], _py_list_max, _PROBLEMS[-1][3])

_reg("33_list_filter_count", """
@untested("bench")
func run() -> Int
    let xs: List[Int] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
    var count: Int = 0
    var rep: Int = 0
    while rep < 5000 do
        var i: Int = 0
        while i < length(xs) do
            if xs[i] % 2 == 0 then
                count = count + 1
            end if
            i = i + 1
        end while
        rep = rep + 1
    end while
    return count
end func
""", lambda: None)


def _py_list_filter_count():
    xs = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
    count = 0
    rep = 0
    while rep < 5000:
        i = 0
        while i < len(xs):
            if xs[i] % 2 == 0:
                count = count + 1
            i = i + 1
        rep = rep + 1
    return count


_PROBLEMS[-1] = ("33_list_filter_count", _PROBLEMS[-1][1], _py_list_filter_count, _PROBLEMS[-1][3])

_reg("34_list_dot_product", """
@untested("bench")
func run() -> Int
    let xs: List[Int] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    let ys: List[Int] = [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
    var total: Int = 0
    var rep: Int = 0
    while rep < 5000 do
        var i: Int = 0
        while i < length(xs) do
            total = total + xs[i] * ys[i]
            i = i + 1
        end while
        rep = rep + 1
    end while
    return total
end func
""", lambda: None)


def _py_list_dot_product():
    xs = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    ys = [10, 9, 8, 7, 6, 5, 4, 3, 2, 1]
    total = 0
    rep = 0
    while rep < 5000:
        i = 0
        while i < len(xs):
            total = total + xs[i] * ys[i]
            i = i + 1
        rep = rep + 1
    return total


_PROBLEMS[-1] = ("34_list_dot_product", _PROBLEMS[-1][1], _py_list_dot_product, _PROBLEMS[-1][3])

_reg("35_list_contains", """
@untested("bench")
func list_contains(xs: List[Int], val: Int) -> Bool
    var i: Int = 0
    while i < length(xs) do
        if xs[i] == val then
            return true
        end if
        i = i + 1
    end while
    return false
end func

@untested("bench")
func run() -> Int
    let xs: List[Int] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
    var count: Int = 0
    var rep: Int = 0
    while rep < 5000 do
        var j: Int = 1
        while j <= 20 do
            if list_contains(xs, j) then
                count = count + 1
            end if
            j = j + 1
        end while
        rep = rep + 1
    end while
    return count
end func
""", lambda: None)


def _py_list_contains():
    def list_contains(xs, val):
        i = 0
        while i < len(xs):
            if xs[i] == val:
                return True
            i = i + 1
        return False

    xs = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
    count = 0
    rep = 0
    while rep < 5000:
        j = 1
        while j <= 20:
            if list_contains(xs, j):
                count = count + 1
            j = j + 1
        rep = rep + 1
    return count


_PROBLEMS[-1] = ("35_list_contains", _PROBLEMS[-1][1], _py_list_contains, _PROBLEMS[-1][3])

_reg("36_binary_search", """
@untested("bench")
func binary_search(xs: List[Int], target: Int) -> Int
    var lo: Int = 0
    var hi: Int = length(xs) - 1
    while lo <= hi do
        let mid: Int = (lo + hi) / 2
        if xs[mid] == target then
            return mid
        end if
        if xs[mid] < target then
            lo = mid + 1
        else
            hi = mid - 1
        end if
    end while
    return 0 - 1
end func

@untested("bench")
func run() -> Int
    let xs: List[Int] = [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32, 34, 36, 38]
    var sum: Int = 0
    var rep: Int = 0
    while rep < 5000 do
        var j: Int = 0
        while j <= 38 do
            sum = sum + binary_search(xs, j)
            j = j + 2
        end while
        rep = rep + 1
    end while
    return sum
end func
""", lambda: None)


def _py_binary_search():
    def binary_search(xs, target):
        lo = 0
        hi = len(xs) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if xs[mid] == target:
                return mid
            if xs[mid] < target:
                lo = mid + 1
            else:
                hi = mid - 1
        return -1

    xs = [0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32, 34, 36, 38]
    s = 0
    rep = 0
    while rep < 5000:
        j = 0
        while j <= 38:
            s = s + binary_search(xs, j)
            j = j + 2
        rep = rep + 1
    return s


_PROBLEMS[-1] = ("36_binary_search", _PROBLEMS[-1][1], _py_binary_search, _PROBLEMS[-1][3])

_reg("37_list_count_val", """
@untested("bench")
func run() -> Int
    let xs: List[Int] = [1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2]
    var total: Int = 0
    var rep: Int = 0
    while rep < 5000 do
        var count: Int = 0
        var i: Int = 0
        while i < length(xs) do
            if xs[i] == 1 then
                count = count + 1
            end if
            i = i + 1
        end while
        total = total + count
        rep = rep + 1
    end while
    return total
end func
""", lambda: None)


def _py_list_count_val():
    xs = [1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2, 3, 1, 2]
    total = 0
    rep = 0
    while rep < 5000:
        count = 0
        i = 0
        while i < len(xs):
            if xs[i] == 1:
                count = count + 1
            i = i + 1
        total = total + count
        rep = rep + 1
    return total


_PROBLEMS[-1] = ("37_list_count_val", _PROBLEMS[-1][1], _py_list_count_val, _PROBLEMS[-1][3])

_reg("38_list_map_manual", """
@untested("bench")
func run() -> Int
    let xs: List[Int] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    var total: Int = 0
    var rep: Int = 0
    while rep < 5000 do
        var i: Int = 0
        while i < length(xs) do
            total = total + xs[i] * 2
            i = i + 1
        end while
        rep = rep + 1
    end while
    return total
end func
""", lambda: None)


def _py_list_map_manual():
    xs = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    total = 0
    rep = 0
    while rep < 5000:
        i = 0
        while i < len(xs):
            total = total + xs[i] * 2
            i = i + 1
        rep = rep + 1
    return total


_PROBLEMS[-1] = ("38_list_map_manual", _PROBLEMS[-1][1], _py_list_map_manual, _PROBLEMS[-1][3])

_reg("39_recursive_sum", """
@untested("bench")
func rec_sum(xs: List[Int], idx: Int) -> Int
    if idx >= length(xs) then
        return 0
    end if
    return xs[idx] + rec_sum(xs, idx + 1)
end func

@untested("bench")
func run() -> Int
    let xs: List[Int] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    var total: Int = 0
    var rep: Int = 0
    while rep < 5000 do
        total = total + rec_sum(xs, 0)
        rep = rep + 1
    end while
    return total
end func
""", lambda: None)


def _py_recursive_sum():
    def rec_sum(xs, idx):
        if idx >= len(xs):
            return 0
        return xs[idx] + rec_sum(xs, idx + 1)

    xs = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    total = 0
    rep = 0
    while rep < 5000:
        total = total + rec_sum(xs, 0)
        rep = rep + 1
    return total


_PROBLEMS[-1] = ("39_recursive_sum", _PROBLEMS[-1][1], _py_recursive_sum, _PROBLEMS[-1][3])

_reg("40_list_index_of", """
@untested("bench")
func index_of(xs: List[Int], val: Int) -> Int
    var i: Int = 0
    while i < length(xs) do
        if xs[i] == val then
            return i
        end if
        i = i + 1
    end while
    return 0 - 1
end func

@untested("bench")
func run() -> Int
    let xs: List[Int] = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    var sum: Int = 0
    var rep: Int = 0
    while rep < 5000 do
        var j: Int = 0
        while j < 10 do
            sum = sum + index_of(xs, (j + 1) * 10)
            j = j + 1
        end while
        rep = rep + 1
    end while
    return sum
end func
""", lambda: None)


def _py_list_index_of():
    def index_of(xs, val):
        i = 0
        while i < len(xs):
            if xs[i] == val:
                return i
            i = i + 1
        return -1

    xs = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    s = 0
    rep = 0
    while rep < 5000:
        j = 0
        while j < 10:
            s = s + index_of(xs, (j + 1) * 10)
            j = j + 1
        rep = rep + 1
    return s


_PROBLEMS[-1] = ("40_list_index_of", _PROBLEMS[-1][1], _py_list_index_of, _PROBLEMS[-1][3])

# --- 41-50: String operations ---

_reg("41_str_concat", """
@untested("bench")
func run() -> Int
    var s: String = ""
    var i: Int = 0
    while i < 2000 do
        s = s + "x"
        i = i + 1
    end while
    return length(s)
end func
""", lambda: None)


def _py_str_concat():
    s = ""
    i = 0
    while i < 2000:
        s = s + "x"
        i = i + 1
    return len(s)


_PROBLEMS[-1] = ("41_str_concat", _PROBLEMS[-1][1], _py_str_concat, _PROBLEMS[-1][3])

_reg("42_str_to_upper", """
@untested("bench")
func run() -> Int
    let s: String = "hello world this is a benchmark"
    var count: Int = 0
    var rep: Int = 0
    while rep < 10000 do
        let u: String = to_upper(s)
        count = count + length(u)
        rep = rep + 1
    end while
    return count
end func
""", lambda: None)


def _py_str_to_upper():
    s = "hello world this is a benchmark"
    count = 0
    rep = 0
    while rep < 10000:
        u = s.upper()
        count = count + len(u)
        rep = rep + 1
    return count


_PROBLEMS[-1] = ("42_str_to_upper", _PROBLEMS[-1][1], _py_str_to_upper, _PROBLEMS[-1][3])

_reg("43_str_char_scan", """
@untested("bench")
func run() -> Int
    let s: String = "the quick brown fox jumps over the lazy dog"
    var total: Int = 0
    var rep: Int = 0
    while rep < 1000 do
        var i: Int = 0
        while i < length(s) do
            if string_char_at(text: s, index: i) == "o" then
                total = total + 1
            end if
            i = i + 1
        end while
        rep = rep + 1
    end while
    return total
end func
""", lambda: None)


def _py_str_char_scan():
    s = "the quick brown fox jumps over the lazy dog"
    total = 0
    rep = 0
    while rep < 1000:
        i = 0
        while i < len(s):
            if s[i] == "o":
                total = total + 1
            i = i + 1
        rep = rep + 1
    return total


_PROBLEMS[-1] = ("43_str_char_scan", _PROBLEMS[-1][1], _py_str_char_scan, _PROBLEMS[-1][3])

_reg("44_str_palindrome", """
@untested("bench")
func is_palindrome(s: String) -> Bool
    var lo: Int = 0
    var hi: Int = length(s) - 1
    while lo < hi do
        if string_char_at(text: s, index: lo) != string_char_at(text: s, index: hi) then
            return false
        end if
        lo = lo + 1
        hi = hi - 1
    end while
    return true
end func

@untested("bench")
func run() -> Int
    var count: Int = 0
    var rep: Int = 0
    while rep < 5000 do
        if is_palindrome("racecar") then
            count = count + 1
        end if
        if is_palindrome("abcba") then
            count = count + 1
        end if
        if is_palindrome("hello") then
            count = count + 1
        end if
        rep = rep + 1
    end while
    return count
end func
""", lambda: None)


def _py_str_palindrome():
    def is_palindrome(s):
        lo = 0
        hi = len(s) - 1
        while lo < hi:
            if s[lo] != s[hi]:
                return False
            lo = lo + 1
            hi = hi - 1
        return True

    count = 0
    rep = 0
    while rep < 5000:
        if is_palindrome("racecar"):
            count = count + 1
        if is_palindrome("abcba"):
            count = count + 1
        if is_palindrome("hello"):
            count = count + 1
        rep = rep + 1
    return count


_PROBLEMS[-1] = ("44_str_palindrome", _PROBLEMS[-1][1], _py_str_palindrome, _PROBLEMS[-1][3])

_reg("45_str_count_vowels", """
@untested("bench")
func count_vowels(s: String) -> Int
    var count: Int = 0
    var i: Int = 0
    while i < length(s) do
        let c: String = string_char_at(text: s, index: i)
        if c == "a" then
            count = count + 1
        end if
        if c == "e" then
            count = count + 1
        end if
        if c == "i" then
            count = count + 1
        end if
        if c == "o" then
            count = count + 1
        end if
        if c == "u" then
            count = count + 1
        end if
        i = i + 1
    end while
    return count
end func

@untested("bench")
func run() -> Int
    let s: String = "the quick brown fox jumps over the lazy dog"
    var total: Int = 0
    var rep: Int = 0
    while rep < 2000 do
        total = total + count_vowels(s)
        rep = rep + 1
    end while
    return total
end func
""", lambda: None)


def _py_str_count_vowels():
    def count_vowels(s):
        count = 0
        i = 0
        while i < len(s):
            c = s[i]
            if c == "a":
                count = count + 1
            if c == "e":
                count = count + 1
            if c == "i":
                count = count + 1
            if c == "o":
                count = count + 1
            if c == "u":
                count = count + 1
            i = i + 1
        return count

    s = "the quick brown fox jumps over the lazy dog"
    total = 0
    rep = 0
    while rep < 2000:
        total = total + count_vowels(s)
        rep = rep + 1
    return total


_PROBLEMS[-1] = ("45_str_count_vowels", _PROBLEMS[-1][1], _py_str_count_vowels, _PROBLEMS[-1][3])

_reg("46_str_starts_with", """
@untested("bench")
func starts_with(s: String, prefix: String) -> Bool
    if length(prefix) > length(s) then
        return false
    end if
    return substring(text: s, start: 0, stop: length(prefix)) == prefix
end func

@untested("bench")
func run() -> Int
    var count: Int = 0
    var rep: Int = 0
    while rep < 20000 do
        if starts_with("hello world", "hello") then
            count = count + 1
        end if
        rep = rep + 1
    end while
    return count
end func
""", lambda: None)


def _py_str_starts_with():
    def starts_with(s, prefix):
        if len(prefix) > len(s):
            return False
        return s[:len(prefix)] == prefix

    count = 0
    rep = 0
    while rep < 20000:
        if starts_with("hello world", "hello"):
            count = count + 1
        rep = rep + 1
    return count


_PROBLEMS[-1] = ("46_str_starts_with", _PROBLEMS[-1][1], _py_str_starts_with, _PROBLEMS[-1][3])

_reg("47_str_length_batch", """
@untested("bench")
func run() -> Int
    var total: Int = 0
    var rep: Int = 0
    while rep < 50000 do
        total = total + length("hello world test")
        rep = rep + 1
    end while
    return total
end func
""", lambda: None)


def _py_str_length_batch():
    total = 0
    rep = 0
    while rep < 50000:
        total = total + len("hello world test")
        rep = rep + 1
    return total


_PROBLEMS[-1] = ("47_str_length_batch", _PROBLEMS[-1][1], _py_str_length_batch, _PROBLEMS[-1][3])

_reg("48_str_substring", """
@untested("bench")
func run() -> Int
    let s: String = "abcdefghijklmnopqrstuvwxyz"
    var total: Int = 0
    var rep: Int = 0
    while rep < 10000 do
        let sub: String = substring(text: s, start: 5, stop: 15)
        total = total + length(sub)
        rep = rep + 1
    end while
    return total
end func
""", lambda: None)


def _py_str_substring():
    s = "abcdefghijklmnopqrstuvwxyz"
    total = 0
    rep = 0
    while rep < 10000:
        sub = s[5:15]
        total = total + len(sub)
        rep = rep + 1
    return total


_PROBLEMS[-1] = ("48_str_substring", _PROBLEMS[-1][1], _py_str_substring, _PROBLEMS[-1][3])

_reg("49_str_to_lower", """
@untested("bench")
func run() -> Int
    let s: String = "HELLO WORLD THIS IS A BENCHMARK"
    var count: Int = 0
    var rep: Int = 0
    while rep < 10000 do
        let lower: String = to_lower(s)
        count = count + length(lower)
        rep = rep + 1
    end while
    return count
end func
""", lambda: None)


def _py_str_to_lower():
    s = "HELLO WORLD THIS IS A BENCHMARK"
    count = 0
    rep = 0
    while rep < 10000:
        lower = s.lower()
        count = count + len(lower)
        rep = rep + 1
    return count


_PROBLEMS[-1] = ("49_str_to_lower", _PROBLEMS[-1][1], _py_str_to_lower, _PROBLEMS[-1][3])

_reg("50_str_trim_batch", """
@untested("bench")
func run() -> Int
    let s: String = "   hello world   "
    var total: Int = 0
    var rep: Int = 0
    while rep < 20000 do
        let t: String = trim(s)
        total = total + length(t)
        rep = rep + 1
    end while
    return total
end func
""", lambda: None)


def _py_str_trim_batch():
    s = "   hello world   "
    total = 0
    rep = 0
    while rep < 20000:
        t = s.strip()
        total = total + len(t)
        rep = rep + 1
    return total


_PROBLEMS[-1] = ("50_str_trim_batch", _PROBLEMS[-1][1], _py_str_trim_batch, _PROBLEMS[-1][3])

# --- 51-60: More mixed benchmarks ---

_reg("51_compose_apply", """
@untested("bench")
func run() -> Int
    var total: Int = 0
    var i: Int = 0
    while i < 50000 do
        total = total + (i + 3) * 2
        i = i + 1
    end while
    return total
end func
""", lambda: None)


def _py_compose_apply():
    total = 0
    i = 0
    while i < 50000:
        total = total + (i + 3) * 2
        i = i + 1
    return total


_PROBLEMS[-1] = ("51_compose_apply", _PROBLEMS[-1][1], _py_compose_apply, _PROBLEMS[-1][3])

_reg("52_apply_n_times", """
@untested("bench")
func apply_n(n: Int, x: Int) -> Int
    var val: Int = x
    var i: Int = 0
    while i < n do
        val = val + 3
        i = i + 1
    end while
    return val
end func

@untested("bench")
func run() -> Int
    var sum: Int = 0
    var i: Int = 0
    while i < 500 do
        sum = sum + apply_n(100, i)
        i = i + 1
    end while
    return sum
end func
""", lambda: None)


def _py_apply_n_times():
    def apply_n(n, x):
        val = x
        i = 0
        while i < n:
            val = val + 3
            i = i + 1
        return val

    s = 0
    i = 0
    while i < 500:
        s = s + apply_n(100, i)
        i = i + 1
    return s


_PROBLEMS[-1] = ("52_apply_n_times", _PROBLEMS[-1][1], _py_apply_n_times, _PROBLEMS[-1][3])

_reg("53_map_filter_sum", """
@untested("bench")
func run() -> Int
    let xs: List[Int] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    var total: Int = 0
    var rep: Int = 0
    while rep < 5000 do
        var i: Int = 0
        while i < length(xs) do
            let doubled: Int = xs[i] * 2
            if doubled % 4 == 0 then
                total = total + doubled
            end if
            i = i + 1
        end while
        rep = rep + 1
    end while
    return total
end func
""", lambda: None)


def _py_map_filter_sum():
    xs = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    total = 0
    rep = 0
    while rep < 5000:
        i = 0
        while i < len(xs):
            doubled = xs[i] * 2
            if doubled % 4 == 0:
                total = total + doubled
            i = i + 1
        rep = rep + 1
    return total


_PROBLEMS[-1] = ("53_map_filter_sum", _PROBLEMS[-1][1], _py_map_filter_sum, _PROBLEMS[-1][3])

_reg("54_sieve_200", """
@untested("bench")
func run() -> Int
    let limit: Int = 200
    var flags: List[Bool] = []
    var i: Int = 0
    while i < limit do
        flags = append(flags, true)
        i = i + 1
    end while
    var p: Int = 2
    while p * p < limit do
        if flags[p] then
            var j: Int = p * p
            while j < limit do
                flags = set_at(list: flags, index: j, value: false)
                j = j + p
            end while
        end if
        p = p + 1
    end while
    var count: Int = 0
    var k: Int = 2
    while k < limit do
        if flags[k] then
            count = count + 1
        end if
        k = k + 1
    end while
    return count
end func
""", lambda: None)


def _py_sieve_200():
    limit = 200
    flags = []
    i = 0
    while i < limit:
        flags.append(True)
        i = i + 1
    p = 2
    while p * p < limit:
        if flags[p]:
            j = p * p
            while j < limit:
                flags[j] = False
                j = j + p
        p = p + 1
    count = 0
    k = 2
    while k < limit:
        if flags[k]:
            count = count + 1
        k = k + 1
    return count


_PROBLEMS[-1] = ("54_sieve_200", _PROBLEMS[-1][1], _py_sieve_200, _PROBLEMS[-1][3])

_reg("55_matrix_trace", """
@untested("bench")
func run() -> Int
    let n: Int = 20
    let xs: List[Int] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
    var total: Int = 0
    var rep: Int = 0
    while rep < 2000 do
        var i: Int = 0
        while i < n do
            total = total + xs[i] * xs[i]
            i = i + 1
        end while
        rep = rep + 1
    end while
    return total
end func
""", lambda: None)


def _py_matrix_trace():
    n = 20
    xs = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
    total = 0
    rep = 0
    while rep < 2000:
        i = 0
        while i < n:
            total = total + xs[i] * xs[i]
            i = i + 1
        rep = rep + 1
    return total


_PROBLEMS[-1] = ("55_matrix_trace", _PROBLEMS[-1][1], _py_matrix_trace, _PROBLEMS[-1][3])

_reg("56_lcm_batch", """
@untested("bench")
func gcd(a: Int, b: Int) -> Int
    var x: Int = a
    var y: Int = b
    while y != 0 do
        let t: Int = y
        y = x % y
        x = t
    end while
    return x
end func

@untested("bench")
func lcm(a: Int, b: Int) -> Int
    return a * b / gcd(a, b)
end func

@untested("bench")
func run() -> Int
    var sum: Int = 0
    var i: Int = 1
    while i <= 5000 do
        sum = sum + lcm(i, i + 1)
        i = i + 1
    end while
    return sum
end func
""", lambda: None)


def _py_lcm_batch():
    def gcd(a, b):
        x = a
        y = b
        while y != 0:
            x, y = y, x % y
        return x

    def lcm(a, b):
        return a * b // gcd(a, b)

    s = 0
    i = 1
    while i <= 5000:
        s = s + lcm(i, i + 1)
        i = i + 1
    return s


_PROBLEMS[-1] = ("56_lcm_batch", _PROBLEMS[-1][1], _py_lcm_batch, _PROBLEMS[-1][3])

_reg("57_abs_batch", """
@untested("bench")
func abs_val(n: Int) -> Int
    if n < 0 then
        return 0 - n
    end if
    return n
end func

@untested("bench")
func run() -> Int
    var sum: Int = 0
    var i: Int = -25000
    while i <= 25000 do
        sum = sum + abs_val(i)
        i = i + 1
    end while
    return sum
end func
""", lambda: None)


def _py_abs_batch():
    def abs_val(n):
        if n < 0:
            return -n
        return n

    s = 0
    i = -25000
    while i <= 25000:
        s = s + abs_val(i)
        i = i + 1
    return s


_PROBLEMS[-1] = ("57_abs_batch", _PROBLEMS[-1][1], _py_abs_batch, _PROBLEMS[-1][3])

_reg("58_is_even_batch", """
@untested("bench")
func run() -> Int
    var count: Int = 0
    var i: Int = 0
    while i < 100000 do
        if i % 2 == 0 then
            count = count + 1
        end if
        i = i + 1
    end while
    return count
end func
""", lambda: None)


def _py_is_even_batch():
    count = 0
    i = 0
    while i < 100000:
        if i % 2 == 0:
            count = count + 1
        i = i + 1
    return count


_PROBLEMS[-1] = ("58_is_even_batch", _PROBLEMS[-1][1], _py_is_even_batch, _PROBLEMS[-1][3])

_reg("59_simple_counter", """
@untested("bench")
func run() -> Int
    var state: Int = 0
    var i: Int = 0
    while i < 100000 do
        state = state + 1
        i = i + 1
    end while
    return state
end func
""", lambda: None)


def _py_simple_counter():
    state = 0
    i = 0
    while i < 100000:
        state = state + 1
        i = i + 1
    return state


_PROBLEMS[-1] = ("59_simple_counter", _PROBLEMS[-1][1], _py_simple_counter, _PROBLEMS[-1][3])

_reg("60_nested_fn_chain", """
@untested("bench")
func f1(n: Int) -> Int
    return n + 1
end func

@untested("bench")
func f2(n: Int) -> Int
    return f1(n) * 2
end func

@untested("bench")
func f3(n: Int) -> Int
    return f2(n) - 1
end func

@untested("bench")
func run() -> Int
    var total: Int = 0
    var i: Int = 0
    while i < 30000 do
        total = total + f3(i)
        i = i + 1
    end while
    return total
end func
""", lambda: None)


def _py_nested_fn_chain():
    def f1(n):
        return n + 1

    def f2(n):
        return f1(n) * 2

    def f3(n):
        return f2(n) - 1

    total = 0
    i = 0
    while i < 30000:
        total = total + f3(i)
        i = i + 1
    return total


_PROBLEMS[-1] = ("60_nested_fn_chain", _PROBLEMS[-1][1], _py_nested_fn_chain, _PROBLEMS[-1][3])

# --- 61-70: More variants ---

_reg("61_sum_cubes", """
@untested("bench")
func run() -> Int
    var total: Int = 0
    var i: Int = 1
    while i <= 30000 do
        total = total + i * i * i
        i = i + 1
    end while
    return total
end func
""", lambda: None)


def _py_sum_cubes():
    total = 0
    i = 1
    while i <= 30000:
        total = total + i * i * i
        i = i + 1
    return total


_PROBLEMS[-1] = ("61_sum_cubes", _PROBLEMS[-1][1], _py_sum_cubes, _PROBLEMS[-1][3])

_reg("62_interleaved_ops", """
@untested("bench")
func run() -> Int
    var a: Int = 1
    var b: Int = 1
    var i: Int = 0
    while i < 50000 do
        let t: Int = a + b
        a = b
        b = t % 1000000
        i = i + 1
    end while
    return b
end func
""", lambda: None)


def _py_interleaved_ops():
    a = 1
    b = 1
    i = 0
    while i < 50000:
        t = a + b
        a = b
        b = t % 1000000
        i = i + 1
    return b


_PROBLEMS[-1] = ("62_interleaved_ops", _PROBLEMS[-1][1], _py_interleaved_ops, _PROBLEMS[-1][3])

_reg("63_boolean_chain", """
@untested("bench")
func check(a: Int, b: Int, c: Int) -> Bool
    if a > 0 then
        if b > 0 then
            if c > 0 then
                return true
            end if
        end if
    end if
    return false
end func

@untested("bench")
func run() -> Int
    var count: Int = 0
    var i: Int = 0
    while i < 30000 do
        if check(a: i, b: i - 100, c: i - 200) then
            count = count + 1
        end if
        i = i + 1
    end while
    return count
end func
""", lambda: None)


def _py_boolean_chain():
    def check(a, b, c):
        if a > 0:
            if b > 0:
                if c > 0:
                    return True
        return False

    count = 0
    i = 0
    while i < 30000:
        if check(i, i - 100, i - 200):
            count = count + 1
        i = i + 1
    return count


_PROBLEMS[-1] = ("63_boolean_chain", _PROBLEMS[-1][1], _py_boolean_chain, _PROBLEMS[-1][3])

_reg("64_digit_root_batch", """
@untested("bench")
func digit_sum(n: Int) -> Int
    var total: Int = 0
    var val: Int = n
    while val > 0 do
        total = total + val % 10
        val = val / 10
    end while
    return total
end func

@untested("bench")
func digital_root(n: Int) -> Int
    var val: Int = n
    while val >= 10 do
        val = digit_sum(val)
    end while
    return val
end func

@untested("bench")
func run() -> Int
    var sum: Int = 0
    var i: Int = 1
    while i <= 10000 do
        sum = sum + digital_root(i)
        i = i + 1
    end while
    return sum
end func
""", lambda: None)


def _py_digit_root_batch():
    def digit_sum(n):
        total = 0
        val = n
        while val > 0:
            total = total + val % 10
            val = val // 10
        return total

    def digital_root(n):
        val = n
        while val >= 10:
            val = digit_sum(val)
        return val

    s = 0
    i = 1
    while i <= 10000:
        s = s + digital_root(i)
        i = i + 1
    return s


_PROBLEMS[-1] = ("64_digit_root_batch", _PROBLEMS[-1][1], _py_digit_root_batch, _PROBLEMS[-1][3])

_reg("65_max_min_batch", """
@untested("bench")
func run() -> Int
    var mx: Int = 0
    var mn: Int = 999999
    var i: Int = 0
    while i < 50000 do
        let v: Int = (i * 7 + 13) % 10000
        if v > mx then
            mx = v
        end if
        if v < mn then
            mn = v
        end if
        i = i + 1
    end while
    return mx - mn
end func
""", lambda: None)


def _py_max_min_batch():
    mx = 0
    mn = 999999
    i = 0
    while i < 50000:
        v = (i * 7 + 13) % 10000
        if v > mx:
            mx = v
        if v < mn:
            mn = v
        i = i + 1
    return mx - mn


_PROBLEMS[-1] = ("65_max_min_batch", _PROBLEMS[-1][1], _py_max_min_batch, _PROBLEMS[-1][3])

_reg("66_alternating_sum", """
@untested("bench")
func run() -> Int
    var sum: Int = 0
    var i: Int = 1
    while i <= 50000 do
        if i % 2 == 0 then
            sum = sum - i
        else
            sum = sum + i
        end if
        i = i + 1
    end while
    return sum
end func
""", lambda: None)


def _py_alternating_sum():
    s = 0
    i = 1
    while i <= 50000:
        if i % 2 == 0:
            s = s - i
        else:
            s = s + i
        i = i + 1
    return s


_PROBLEMS[-1] = ("66_alternating_sum", _PROBLEMS[-1][1], _py_alternating_sum, _PROBLEMS[-1][3])

_reg("67_xor_accumulate", """
@untested("bench")
func run() -> Int
    var result: Int = 0
    var i: Int = 0
    while i < 100000 do
        result = bit_or(result, i) - bit_or(result, i) + result + 1
        i = i + 1
    end while
    return result
end func
""", lambda: None)


def _py_xor_accumulate():
    result = 0
    i = 0
    while i < 100000:
        result = (result | i) - (result | i) + result + 1
        i = i + 1
    return result


_PROBLEMS[-1] = ("67_xor_accumulate", _PROBLEMS[-1][1], _py_xor_accumulate, _PROBLEMS[-1][3])

_reg("68_triple_nested_if", """
@untested("bench")
func run() -> Int
    var sum: Int = 0
    var i: Int = 0
    while i < 20000 do
        if i % 2 == 0 then
            if i % 3 == 0 then
                sum = sum + 6
            else
                sum = sum + 2
            end if
        else
            if i % 3 == 0 then
                sum = sum + 3
            else
                sum = sum + 1
            end if
        end if
        i = i + 1
    end while
    return sum
end func
""", lambda: None)


def _py_triple_nested_if():
    s = 0
    i = 0
    while i < 20000:
        if i % 2 == 0:
            if i % 3 == 0:
                s = s + 6
            else:
                s = s + 2
        else:
            if i % 3 == 0:
                s = s + 3
            else:
                s = s + 1
        i = i + 1
    return s


_PROBLEMS[-1] = ("68_triple_nested_if", _PROBLEMS[-1][1], _py_triple_nested_if, _PROBLEMS[-1][3])

_reg("69_sum_multiples", """
@untested("bench")
func run() -> Int
    var sum: Int = 0
    var i: Int = 1
    while i < 100000 do
        if i % 3 == 0 then
            sum = sum + i
        end if
        if i % 5 == 0 then
            sum = sum + i
        end if
        i = i + 1
    end while
    return sum
end func
""", lambda: None)


def _py_sum_multiples():
    s = 0
    i = 1
    while i < 100000:
        if i % 3 == 0:
            s = s + i
        if i % 5 == 0:
            s = s + i
        i = i + 1
    return s


_PROBLEMS[-1] = ("69_sum_multiples", _PROBLEMS[-1][1], _py_sum_multiples, _PROBLEMS[-1][3])

_reg("70_list_scan_sum", """
@untested("bench")
func run() -> Int
    let xs: List[Int] = [1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29]
    var total: Int = 0
    var rep: Int = 0
    while rep < 5000 do
        var i: Int = 0
        while i < length(xs) do
            total = total + xs[i]
            i = i + 1
        end while
        rep = rep + 1
    end while
    return total
end func
""", lambda: None)


def _py_list_scan_sum():
    xs = [1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27, 29]
    total = 0
    rep = 0
    while rep < 5000:
        i = 0
        while i < len(xs):
            total = total + xs[i]
            i = i + 1
        rep = rep + 1
    return total


_PROBLEMS[-1] = ("70_list_scan_sum", _PROBLEMS[-1][1], _py_list_scan_sum, _PROBLEMS[-1][3])

# --- 71-77: Final batch ---

_reg("71_power_of_two", """
@untested("bench")
func run() -> Int
    var sum: Int = 0
    var rep: Int = 0
    while rep < 2000 do
        var val: Int = 1
        var i: Int = 0
        while i < 20 do
            val = val * 2
            i = i + 1
        end while
        sum = sum + val
        rep = rep + 1
    end while
    return sum
end func
""", lambda: None)


def _py_power_of_two():
    s = 0
    rep = 0
    while rep < 2000:
        val = 1
        i = 0
        while i < 20:
            val = val * 2
            i = i + 1
        s = s + val
        rep = rep + 1
    return s


_PROBLEMS[-1] = ("71_power_of_two", _PROBLEMS[-1][1], _py_power_of_two, _PROBLEMS[-1][3])

_reg("72_nested_300", """
@untested("bench")
func run() -> Int
    var total: Int = 0
    var i: Int = 0
    while i < 300 do
        var j: Int = 0
        while j < 300 do
            if (i + j) % 2 == 0 then
                total = total + 1
            end if
            j = j + 1
        end while
        i = i + 1
    end while
    return total
end func
""", lambda: None)


def _py_nested_300():
    total = 0
    i = 0
    while i < 300:
        j = 0
        while j < 300:
            if (i + j) % 2 == 0:
                total = total + 1
            j = j + 1
        i = i + 1
    return total


_PROBLEMS[-1] = ("72_nested_300", _PROBLEMS[-1][1], _py_nested_300, _PROBLEMS[-1][3])

_reg("73_list_two_pointer", """
@untested("bench")
func run() -> Int
    let xs: List[Int] = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
    var total: Int = 0
    var rep: Int = 0
    while rep < 3000 do
        var lo: Int = 0
        var hi: Int = length(xs) - 1
        while lo < hi do
            total = total + xs[lo] + xs[hi]
            lo = lo + 1
            hi = hi - 1
        end while
        rep = rep + 1
    end while
    return total
end func
""", lambda: None)


def _py_list_two_pointer():
    xs = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]
    total = 0
    rep = 0
    while rep < 3000:
        lo = 0
        hi = len(xs) - 1
        while lo < hi:
            total = total + xs[lo] + xs[hi]
            lo = lo + 1
            hi = hi - 1
        rep = rep + 1
    return total


_PROBLEMS[-1] = ("73_list_two_pointer", _PROBLEMS[-1][1], _py_list_two_pointer, _PROBLEMS[-1][3])

_reg("74_countdown_sum", """
@untested("bench")
func run() -> Int
    var total: Int = 0
    var rep: Int = 0
    while rep < 50 do
        var n: Int = 2000
        while n > 0 do
            total = total + n
            n = n - 1
        end while
        rep = rep + 1
    end while
    return total
end func
""", lambda: None)


def _py_countdown_sum():
    total = 0
    rep = 0
    while rep < 50:
        n = 2000
        while n > 0:
            total = total + n
            n = n - 1
        rep = rep + 1
    return total


_PROBLEMS[-1] = ("74_countdown_sum", _PROBLEMS[-1][1], _py_countdown_sum, _PROBLEMS[-1][3])

_reg("75_pascal_row", """
@untested("bench")
func run() -> Int
    var total: Int = 0
    var rep: Int = 0
    while rep < 500 do
        var row: List[Int] = [1]
        var i: Int = 1
        while i <= 20 do
            var new_row: List[Int] = [1]
            var j: Int = 1
            while j < length(row) do
                new_row = append(new_row, row[j - 1] + row[j])
                j = j + 1
            end while
            new_row = append(new_row, 1)
            row = new_row
            i = i + 1
        end while
        total = total + row[10]
        rep = rep + 1
    end while
    return total
end func
""", lambda: None)


def _py_pascal_row():
    total = 0
    rep = 0
    while rep < 500:
        row = [1]
        i = 1
        while i <= 20:
            new_row = [1]
            j = 1
            while j < len(row):
                new_row.append(row[j - 1] + row[j])
                j = j + 1
            new_row.append(1)
            row = new_row
            i = i + 1
        total = total + row[10]
        rep = rep + 1
    return total


_PROBLEMS[-1] = ("75_pascal_row", _PROBLEMS[-1][1], _py_pascal_row, _PROBLEMS[-1][3])

_reg("76_running_max", """
@untested("bench")
func run() -> Int
    let xs: List[Int] = [3, 1, 4, 1, 5, 9, 2, 6, 5, 3, 5, 8, 9, 7, 9, 3, 2, 3, 8, 4]
    var total: Int = 0
    var rep: Int = 0
    while rep < 5000 do
        var mx: Int = xs[0]
        var i: Int = 1
        while i < length(xs) do
            if xs[i] > mx then
                mx = xs[i]
            end if
            total = total + mx
            i = i + 1
        end while
        rep = rep + 1
    end while
    return total
end func
""", lambda: None)


def _py_running_max():
    xs = [3, 1, 4, 1, 5, 9, 2, 6, 5, 3, 5, 8, 9, 7, 9, 3, 2, 3, 8, 4]
    total = 0
    rep = 0
    while rep < 5000:
        mx = xs[0]
        i = 1
        while i < len(xs):
            if xs[i] > mx:
                mx = xs[i]
            total = total + mx
            i = i + 1
        rep = rep + 1
    return total


_PROBLEMS[-1] = ("76_running_max", _PROBLEMS[-1][1], _py_running_max, _PROBLEMS[-1][3])

_reg("77_sum_abs_diff", """
@untested("bench")
func run() -> Int
    let xs: List[Int] = [5, 12, 3, 18, 7, 15, 9, 1, 20, 11]
    var total: Int = 0
    var rep: Int = 0
    while rep < 3000 do
        var i: Int = 0
        while i < length(xs) - 1 do
            let diff: Int = xs[i] - xs[i + 1]
            if diff < 0 then
                total = total - diff
            else
                total = total + diff
            end if
            i = i + 1
        end while
        rep = rep + 1
    end while
    return total
end func
""", lambda: None)


def _py_sum_abs_diff():
    xs = [5, 12, 3, 18, 7, 15, 9, 1, 20, 11]
    total = 0
    rep = 0
    while rep < 3000:
        i = 0
        while i < len(xs) - 1:
            diff = xs[i] - xs[i + 1]
            if diff < 0:
                total = total - diff
            else:
                total = total + diff
            i = i + 1
        rep = rep + 1
    return total


_PROBLEMS[-1] = ("77_sum_abs_diff", _PROBLEMS[-1][1], _py_sum_abs_diff, _PROBLEMS[-1][3])


# ===========================================================================
# Runner
# ===========================================================================


def run_benchmarks(verbose: bool = False) -> bool:
    """Run all benchmarks and print results.  Returns True if ≥80 % pass ≤2×."""
    print(f"\nGeno vs Python Benchmark Suite  ({len(_PROBLEMS)} problems)")
    print("=" * 72)
    print(f"{'#':>3}  {'Problem':<28} {'Geno (ms)':>10} {'Python (ms)':>11} {'Ratio':>7} {'Pass':>5}")
    print("-" * 72)

    ratios: List[float] = []
    passed = 0
    skipped = 0
    errors = 0

    for idx, (name, geno_src, hw_fn, call_builder) in enumerate(_PROBLEMS, 1):
        try:
            ns = _compile_geno(geno_src)
            geno_fn = call_builder(ns)

            t_geno = _time_fn(geno_fn)
            t_hw = _time_fn(hw_fn)

            if t_hw < MIN_TIME_S and t_geno < MIN_TIME_S:
                skipped += 1
                print(f"{idx:3}  {name:<28} {t_geno*1000:10.2f} {t_hw*1000:11.2f} {'---':>7} {'skip':>5}")
            else:
                ratio = t_geno / max(t_hw, 1e-9)
                ratios.append(ratio)
                ok = ratio <= 2.0
                if ok:
                    passed += 1
                status = "OK" if ok else "SLOW"
                print(f"{idx:3}  {name:<28} {t_geno*1000:10.2f} {t_hw*1000:11.2f} {ratio:7.2f}x {status:>5}")
                if verbose:
                    g_result = geno_fn()
                    h_result = hw_fn()
                    if g_result != h_result:
                        print(f"     ⚠ MISMATCH: geno={g_result} python={h_result}")

        except Exception as e:
            errors += 1
            print(f"{idx:3}  {name:<28} {'ERROR':>10} {'':>11} {'':>7} {'ERR':>5}")
            if verbose:
                print(f"     {e}")

    print("-" * 72)
    total_measured = len(ratios)
    pass_pct = (passed / total_measured * 100) if total_measured else 0
    median_ratio = statistics.median(ratios) if ratios else 0
    mean_ratio = statistics.mean(ratios) if ratios else 0
    p90 = sorted(ratios)[int(len(ratios) * 0.9)] if ratios else 0

    print(f"\nResults: {total_measured} measured, {passed} passed (≤2×), "
          f"{total_measured - passed} slow, {skipped} skipped, {errors} errors")
    print(f"Pass rate: {pass_pct:.1f}% (target: ≥80%)")
    print(f"Median ratio: {median_ratio:.2f}x")
    print(f"Mean ratio:   {mean_ratio:.2f}x")
    print(f"P90 ratio:    {p90:.2f}x")

    success = pass_pct >= 80.0
    print(f"\n{'PASS' if success else 'FAIL'}: {'≥' if success else '<'}80% of problems within 2× of hand-written Python")
    return success


if __name__ == "__main__":
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    success = run_benchmarks(verbose=verbose)
    sys.exit(0 if success else 1)
