#!/usr/bin/env python3
"""
Sandbox Escape Bounty Runner
==============================

Runs adversarial Python snippets through the Geno sandbox to verify
that no escape is possible.  Also re-runs every .geno corpus file.

Exit code 0  → all attacks blocked
Exit code 1  → at least one escape succeeded

Usage:
    python3 scripts/security_bounty.py [-v]
"""

import argparse
import os
import re
import sys
import traceback

# Ensure the project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from geno.api import RunConfig, run
from geno.sandbox import (
    ProcessSandboxConfig,
    SecurityViolation,
    run_in_process,
    validate_code_safety,
)

# ---------------------------------------------------------------------------
# Adversarial Python snippets (raw Python, NOT Geno)
# ---------------------------------------------------------------------------
# Each snippet is (name, code).  A snippet "passes" the bounty if execution
# raises SecurityViolation, produces a sandbox error, or the static validator
# catches it.  If the snippet runs to completion and returns a non-error
# result, that's an escape.

TIER_1_DIRECT = [
    ("eval_literal", 'eval("1+1")'),
    ("exec_literal", 'exec("x=1")'),
    ("import_os", "import os; os.system('echo pwned')"),
    ("import_subprocess", "import subprocess; subprocess.run(['echo', 'pwned'])"),
    ("import_builtins", "import builtins; builtins.eval('1')"),
    ("open_file", "open('/etc/passwd').read()"),
    ("compile_exec", "exec(compile('1+1', '<x>', 'eval'))"),
    ("__import__os", "__import__('os').system('echo pwned')"),
    ("globals_access", "__result__ = globals()"),
    ("type_three_arg", "Evil = type('Evil', (), {'x': 1}); __result__ = Evil.x"),
]

TIER_2_ATTRIBUTE_CHAINS = [
    (
        "class_bases_subclasses",
        "__result__ = ().__class__.__bases__[0].__subclasses__()",
    ),
    (
        "function_globals",
        "def f(): pass\n__result__ = f.__globals__",
    ),
    (
        "function_code",
        "def f(): pass\n__result__ = f.__code__",
    ),
    (
        "closure_cells",
        "def outer():\n x=1\n def inner(): return x\n return inner\n"
        "__result__ = outer().__closure__[0].cell_contents",
    ),
    (
        "generator_frame",
        "def g(): yield 1\ngen = g()\n__result__ = gen.gi_frame",
    ),
    (
        "traceback_frame",
        "try:\n 1/0\nexcept:\n import sys\n __result__ = sys.exc_info()[2].tb_frame",
    ),
    (
        "dict_class_bases",
        "__result__ = {}.__class__.__bases__[0]",
    ),
    (
        "str_class_mro",
        "__result__ = ''.__class__.__mro__",
    ),
]

TIER_3_FORMAT_AND_MODULE = [
    (
        "format_globals",
        "def f(): pass\n__result__ = '{0.__globals__}'.format(f)",
    ),
    (
        "format_class",
        "__result__ = '{0.__class__}'.format(42)",
    ),
    (
        "format_map_class",
        "__result__ = '{x.__class__}'.format_map({'x': 42})",
    ),
    (
        "getattr_computed",
        "__result__ = getattr((), '__cl' + 'ass__')",
    ),
    (
        "getattr_direct_class",
        "__result__ = getattr((), '__class__')",
    ),
    (
        "module_chain_sys",
        "import typing; __result__ = typing.sys",
    ),
]

TIER_4_ADVANCED = [
    (
        "metaclass_new",
        "class Meta(type):\n"
        "    def __new__(mcs, name, bases, ns):\n"
        "        ns['pwned'] = True\n"
        "        return super().__new__(mcs, name, bases, ns)\n"
        "class Evil(metaclass=Meta): pass\n"
        "__result__ = Evil.pwned",
    ),
    (
        "getattribute_override",
        "class Evil:\n"
        "    def __getattribute__(self, name):\n"
        "        return 'escaped'\n"
        "__result__ = Evil().anything",
    ),
    (
        "del_destructor",
        "class Evil:\n"
        "    def __del__(self):\n"
        "        import os; os.system('echo pwned')\n"
        "e = Evil()\ndel e",
    ),
    (
        "init_subclass_hook",
        "class Base:\n"
        "    def __init_subclass__(cls, **kwargs):\n"
        "        cls.pwned = True\n"
        "class Evil(Base): pass\n"
        "__result__ = Evil.pwned",
    ),
    (
        "set_name_descriptor",
        "class Desc:\n"
        "    def __set_name__(self, owner, name):\n"
        "        owner.pwned = True\n"
        "class Evil:\n"
        "    x = Desc()\n"
        "__result__ = Evil.pwned",
    ),
    (
        "bytes_decode_exec",
        "exec(bytes([112,114,105,110,116,40,49,41]).decode())",
    ),
    (
        "chr_join_eval",
        "eval(''.join(chr(c) for c in [49, 43, 49]))",
    ),
]

ALL_TIERS = [
    ("Tier 1: Direct blocked operations", TIER_1_DIRECT),
    ("Tier 2: Attribute chain escapes", TIER_2_ATTRIBUTE_CHAINS),
    ("Tier 3: Format string & module chain escapes", TIER_3_FORMAT_AND_MODULE),
    ("Tier 4: Advanced attacks", TIER_4_ADVANCED),
]

CORPUS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "geno", "tests", "security_corpus"
)


def _run_snippet(name: str, code: str, verbose: bool) -> bool:
    """Run a single adversarial snippet.  Returns True if blocked, False if escaped."""
    try:
        # First check static validator
        warnings = validate_code_safety(code)
        if warnings:
            if verbose:
                print(f"  [STATIC] {name}: {warnings[0][:60]}...")
            return True

        # Run in process sandbox (strict=True)
        config = ProcessSandboxConfig(timeout=5.0, strict=True)
        result, _output = run_in_process(code, config)

        # Any adversarial snippet that runs to completion is an escape, even if
        # it does not set __result__. Successful execution can still mean
        # observable side effects occurred.
        if verbose:
            print(
                f"  [ESCAPE!] {name}: completed successfully (result={repr(result)[:80]})"
            )
        return False

    except (SecurityViolation, RuntimeError, TimeoutError, OSError) as e:
        if verbose:
            msg = str(e)[:60]
            print(f"  [BLOCKED] {name}: {type(e).__name__}: {msg}")
        return True
    except Exception as e:
        # Any other exception is also a block (sandbox killed it)
        if verbose:
            msg = str(e)[:60]
            print(f"  [BLOCKED] {name}: {type(e).__name__}: {msg}")
        return True


def _run_corpus(verbose: bool) -> tuple[int, int]:
    """Run all .geno corpus files.  Returns (passed, failed)."""
    passed = 0
    failed = 0

    for name in sorted(os.listdir(CORPUS_DIR)):
        if not name.endswith(".geno"):
            continue
        filepath = os.path.join(CORPUS_DIR, name)

        # Parse expected error code
        expected_code = None
        with open(filepath) as f:
            for line in f:
                m = re.match(r"#\s*EXPECT:\s*(E\d+)", line)
                if m:
                    expected_code = m.group(1)
                    break

        if expected_code is None:
            if verbose:
                print(f"  [SKIP] {name}: no EXPECT header")
            continue

        # Read and strip # comments
        with open(filepath) as f:
            lines = f.readlines()
        source = "".join(line for line in lines if not line.lstrip().startswith("#"))

        # Determine config (simplified version of test harness logic)
        config = RunConfig(max_steps=10000, timeout=30.0)
        if "capability_denied" in name:
            config = RunConfig(capabilities=set())
        elif "host_callback_missing" in name:
            cap = "fs"
            if "http" in name:
                cap = "http"
            elif "process" in name:
                cap = "process"
            config = RunConfig(capabilities={cap})
        elif "step_limit" in name:
            config = RunConfig(max_steps=50, timeout=5.0)
        elif "output_limit" in name or "output_flood" in name:
            config = RunConfig(
                capabilities={"print"}, max_steps=1_000_000, timeout=30.0
            )
        elif "linked_list_amplification_bomb" in name:
            config = RunConfig(max_collection_size=10, max_steps=10000, timeout=30.0)
        elif "deep_match_bomb" in name:
            config = RunConfig(max_recursion_depth=100, max_steps=10000, timeout=30.0)

        result = run(source, config=config)

        if not result.ok:
            codes = [d.code.value for d in result.diagnostics]
            if expected_code in codes:
                passed += 1
                if verbose:
                    print(f"  [OK] {name}: got {expected_code}")
            else:
                failed += 1
                if verbose:
                    print(f"  [FAIL] {name}: expected {expected_code}, got {codes}")
        else:
            failed += 1
            if verbose:
                print(
                    f"  [ESCAPE!] {name}: expected {expected_code} but "
                    f"program succeeded (value={result.value})"
                )

    return passed, failed


def main():
    parser = argparse.ArgumentParser(description="Sandbox Escape Bounty Runner")
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Show details for each test"
    )
    args = parser.parse_args()

    verbose = args.verbose
    total_escapes = 0
    total_blocked = 0

    # --- Adversarial Python snippets ---
    print("=" * 60)
    print("Sandbox Escape Bounty Runner")
    print("=" * 60)

    for tier_name, snippets in ALL_TIERS:
        print(f"\n{tier_name}")
        print("-" * len(tier_name))
        tier_blocked = 0
        tier_escaped = 0

        for name, code in snippets:
            if _run_snippet(name, code, verbose):
                tier_blocked += 1
            else:
                tier_escaped += 1

        total_blocked += tier_blocked
        total_escapes += tier_escaped
        status = "PASS" if tier_escaped == 0 else "FAIL"
        print(
            f"  [{status}] {tier_blocked}/{len(snippets)} blocked, "
            f"{tier_escaped} escaped"
        )

    # --- Geno corpus files ---
    print("\nGeno Security Corpus")
    print("-" * 20)
    corpus_passed, corpus_failed = _run_corpus(verbose)
    total_blocked += corpus_passed
    total_escapes += corpus_failed
    status = "PASS" if corpus_failed == 0 else "FAIL"
    print(f"  [{status}] {corpus_passed} blocked, {corpus_failed} escaped")

    # --- Summary ---
    print(f"\n{'=' * 60}")
    print(f"Total: {total_blocked} blocked, {total_escapes} escaped")
    if total_escapes > 0:
        print("RESULT: FAIL — sandbox escapes detected!")
        return 1
    else:
        print("RESULT: PASS — all attacks blocked")
        return 0


if __name__ == "__main__":
    sys.exit(main())
