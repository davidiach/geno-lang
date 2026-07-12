"""
Differential Fuzzing Tests
===========================

Generates valid Geno programs via Hypothesis and verifies identical
observable output across the interpreter, compiled Python, and compiled JS
backends.

Modes:
- Default (smoke): ~60s, 50 generated programs + seed corpus.
- Deep (GENO_FUZZ_DEEP=1 or -m fuzzing_deep): 2000 programs, longer timeouts.

Hypothesis is only required for the generated-program parity class. The
seed-corpus and failure-regression tests, plus the failure-corpus persistence
test, only depend on the local fuzzing.runner / fuzzing.corpus modules and
run unconditionally.
"""
# mypy: disable-error-code="no-redef,misc"

import os
import shutil

import pytest

# corpus and runner have no hypothesis dependency, so import them eagerly.
from geno.tests.fuzzing.corpus import load_failure_corpus, load_seed_corpus
from geno.tests.fuzzing.runner import BackendResult, DiffResult, run_all_backends

try:
    from hypothesis import given, settings
    from hypothesis import strategies as _hypothesis_strategies

    # Trigger AttributeError for partial/shadowed installs (namespace pkgs).
    _ = _hypothesis_strategies.integers
    HYPOTHESIS_AVAILABLE = True
except (ImportError, AttributeError):
    HYPOTHESIS_AVAILABLE = False


HAS_NODE = shutil.which("node") is not None
IS_DEEP = os.environ.get("GENO_FUZZ_DEEP", "") == "1"


# ---------------------------------------------------------------------------
# Smoke mode (default pytest run)
# ---------------------------------------------------------------------------

# TestGeneratedProgramParity must only be defined when hypothesis is available:
# the @given(...) decorator below evaluates a strategy at class-definition
# time, which transitively imports geno.tests.fuzzing.gen (and therefore
# hypothesis). Defining it inside an `if HYPOTHESIS_AVAILABLE` block keeps
# the rest of this module importable without hypothesis.
if HYPOTHESIS_AVAILABLE:

    @pytest.mark.slow
    class TestGeneratedProgramParity:
        """Generated programs produce identical output across all backends."""

        @given(
            program=(
                __import__(
                    "geno.tests.fuzzing.gen", fromlist=["geno_program"]
                ).geno_program()
            )
        )
        @settings(max_examples=50 if not IS_DEEP else 2000, deadline=None)
        def test_generated_programs_agree(self, program):
            """All backends produce identical output for generated programs."""
            result = run_all_backends(
                program.source,
                oracle=program.expected_output,
                timeout=5.0,
                include_js=HAS_NODE,
            )
            successful = [b for b in result.backends if b.success]
            if len(successful) < 2:
                # Not enough backends for a meaningful comparison — skip
                return
            assert result.match, (
                f"Divergence detected:\n{result.error}\n\nSource:\n{program.source}"
            )


@pytest.mark.slow
class TestSeedCorpusParity:
    """All seed corpus programs produce identical output across backends."""

    def test_seed_corpus_agrees(self):
        seeds = load_seed_corpus()
        assert len(seeds) > 0, "Seed corpus is empty"
        for i, source in enumerate(seeds):
            result = run_all_backends(source, timeout=5.0, include_js=HAS_NODE)
            successful = [b for b in result.backends if b.success]
            if len(successful) < 2:
                continue
            assert result.match, (
                f"Seed program {i} diverged:\n{result.error}\n\nSource:\n{source}"
            )


@pytest.mark.slow
class TestFailureRegressions:
    """Previously-found failures still agree (regression check)."""

    def test_failure_regressions(self):
        failures = load_failure_corpus()
        if not failures:
            pytest.skip("No failure corpus files found")
        for i, (source, oracle) in enumerate(failures):
            result = run_all_backends(
                source, oracle=oracle, timeout=5.0, include_js=HAS_NODE
            )
            successful = [b for b in result.backends if b.success]
            if len(successful) < 2:
                continue
            assert result.match, (
                f"Regression in failure {i}:\n{result.error}\n\nSource:\n{source}"
            )


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
def test_cli_draw_program_is_seed_reproducible():
    import random

    # Lazy import: geno.tests.fuzzing.cli pulls in fuzzing.gen which requires
    # hypothesis. Importing here keeps the rest of this module hypothesis-free.
    from geno.tests.fuzzing.cli import _draw_program

    first = _draw_program(random.Random(123)).source
    second = _draw_program(random.Random(123)).source
    third = _draw_program(random.Random(456)).source

    assert first == second
    assert first != third


def test_failure_corpus_preserves_oracle(tmp_path, monkeypatch):
    import geno.tests.fuzzing.corpus as corpus

    monkeypatch.setattr(corpus, "_FAILURES_DIR", tmp_path)
    diff = DiffResult(
        source="func main() -> Unit\n    print(0)\n    return ()\nend func\n",
        oracle="1\n",
        backends=[
            BackendResult(
                name="interpreter",
                stdout="0\n",
                stderr="",
                success=True,
                elapsed_s=0.1,
            )
        ],
        match=False,
        error="oracle mismatch",
    )
    corpus.save_failure(diff)

    loaded = corpus.load_failure_corpus()
    assert loaded == [(diff.source, "1\n")]
