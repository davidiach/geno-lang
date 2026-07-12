"""
Seed corpus and failure persistence for differential fuzzing.

Provides hand-written seed programs covering core Geno features (arithmetic,
conditionals, loops, ADTs, closures, etc.) and saves divergences to
geno/tests/fuzzing/failures/ for regression testing.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .runner import DiffResult

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXAMPLES_DIR = _REPO_ROOT / "examples"
_FAILURES_DIR = Path(__file__).resolve().parent / "failures"

# ---------------------------------------------------------------------------
# Seed corpus
# ---------------------------------------------------------------------------

# Hand-picked example programs that have main() and produce printable output.
# We wrap them so they print observable values via print() and return Unit.
_SEED_PROGRAMS: list[str] = [
    # Basic arithmetic
    """\
func main() -> Unit
    print(1 + 2)
    print(10 - 3)
    print(4 * 5)
    print(10 / 3)
    print(17 % 5)
    return ()
end func
""",
    # Conditionals
    """\
func main() -> Unit
    if 3 > 2 then
        print(1)
    else
        print(0)
    end if
    if 5 == 5 then
        print(1)
    else
        print(0)
    end if
    return ()
end func
""",
    # While loop
    """\
func main() -> Unit
    var sum: Int = 0
    var i: Int = 1
    while i <= 10 do
        sum = sum + i
        i = i + 1
    end while
    print(sum)
    return ()
end func
""",
    # For loop
    """\
func main() -> Unit
    var total: Int = 0
    for x: Int in [1, 2, 3, 4, 5] do
        total = total + x
    end for
    print(total)
    return ()
end func
""",
    # Recursion
    """\
func fib(n: Int) -> Int
    example 0 -> 0
    example 1 -> 1
    if n <= 1 then
        return n
    end if
    return fib(n - 1) + fib(n - 2)
end func

func main() -> Unit
    print(fib(0))
    print(fib(1))
    print(fib(5))
    print(fib(10))
    return ()
end func
""",
    # Higher-order functions
    """\
func main() -> Unit
    let nums: List[Int] = [1, 2, 3, 4, 5]
    let doubled: List[Int] = map(nums, fn(x: Int) -> x * 2)
    print(head(doubled))
    print(length(doubled))
    let evens: List[Int] = filter(nums, fn(x: Int) -> x % 2 == 0)
    print(length(evens))
    let total: Int = fold(list: nums, initial: 0, reducer: fn(acc: Int, x: Int) -> acc + x)
    print(total)
    return ()
end func
""",
    # ADT + pattern matching
    """\
type Color = Red | Green | Blue

func color_code(c: Color) -> Int
    example Red -> 1
    example Green -> 2
    match c with
        | Red -> return 1
        | Green -> return 2
        | Blue -> return 3
    end match
end func

func main() -> Unit
    print(color_code(Red))
    print(color_code(Green))
    print(color_code(Blue))
    return ()
end func
""",
    # Option / Result
    """\
func main() -> Unit
    let maybe: Option[Int] = Some(42)
    match maybe with
        | Some(v) -> print(v)
        | None -> print(0)
    end match
    let outcome: Result[Int, Int] = Ok(7)
    match outcome with
        | Ok(v) -> print(v * 2)
        | Err(e) -> print(0 - e)
    end match
    return ()
end func
""",
    # Closures
    """\
@untested("seed corpus")
func apply(f: (Int) -> Int, x: Int) -> Int
    return f(x)
end func

func main() -> Unit
    let add5: (Int) -> Int = fn(x: Int) -> x + 5
    print(apply(add5, 3))
    print(apply(fn(x: Int) -> x * 2, 6))
    return ()
end func
""",
    # Negative division edge cases
    """\
func main() -> Unit
    print((0 - 7) / 2)
    print(7 / (0 - 2))
    print((0 - 7) / (0 - 2))
    print((0 - 7) % 3)
    print(7 % (0 - 3))
    return ()
end func
""",
    # String operations (lengths only for cross-backend compat)
    """\
func main() -> Unit
    let s: String = "hello" + " " + "world"
    print(length(s))
    let parts: List[String] = split(s, " ")
    print(length(parts))
    return ()
end func
""",
    # List operations
    """\
func main() -> Unit
    let xs: List[Int] = [10, 20, 30, 40, 50]
    print(length(xs))
    print(head(xs))
    print(length(tail(xs)))
    let ys: List[Int] = append(xs, 60)
    print(length(ys))
    let zs: List[Int] = concat([1, 2], [3, 4])
    print(length(zs))
    print(head(zs))
    return ()
end func
""",
]


def load_seed_corpus() -> list[str]:
    """Return a list of seed Geno programs for fuzzing."""
    return list(_SEED_PROGRAMS)


# ---------------------------------------------------------------------------
# Failure persistence
# ---------------------------------------------------------------------------


def save_failure(diff: DiffResult) -> Path:
    """Save a divergence to the failures directory for regression testing.

    Returns the path to the saved .geno file.
    """
    _FAILURES_DIR.mkdir(parents=True, exist_ok=True)
    src_hash = hashlib.sha256(diff.source.encode()).hexdigest()[:12]
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    base = f"failure_{timestamp}_{src_hash}"

    geno_path = _FAILURES_DIR / f"{base}.geno"
    geno_path.write_text(diff.source)

    sidecar = {
        "oracle": diff.oracle,
        "error": diff.error,
        "backends": [
            {
                "name": b.name,
                "stdout": b.stdout,
                "stderr": b.stderr,
                "success": b.success,
                "elapsed_s": b.elapsed_s,
            }
            for b in diff.backends
        ],
    }
    json_path = _FAILURES_DIR / f"{base}.json"
    json_path.write_text(json.dumps(sidecar, indent=2))

    return geno_path


def load_failure_corpus() -> list[tuple[str, str | None]]:
    """Load all previously-saved failure programs for regression testing."""
    if not _FAILURES_DIR.exists():
        return []
    programs: list[tuple[str, str | None]] = []
    for path in sorted(_FAILURES_DIR.glob("*.geno")):
        source = path.read_text()
        oracle = None
        sidecar = path.with_suffix(".json")
        if sidecar.exists():
            try:
                data = json.loads(sidecar.read_text())
                oracle = data.get("oracle")
            except (json.JSONDecodeError, KeyError):
                pass
        programs.append((source, oracle))
    return programs
