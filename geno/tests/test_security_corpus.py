"""
Security Regression Corpus Tests
==================================

Parametrized tests that run each .geno file in the security_corpus/ directory
and verify the expected error code from the ``# EXPECT: Exxx`` header.
"""

import os
import re

import pytest

from geno.api import RunConfig, run
from geno.diagnostics import ErrorCode

CORPUS_DIR = os.path.join(os.path.dirname(__file__), "security_corpus")


def _collect_corpus_files():
    """Collect all .geno files from the security corpus."""
    files = []
    for name in sorted(os.listdir(CORPUS_DIR)):
        if name.endswith(".geno"):
            files.append(os.path.join(CORPUS_DIR, name))
    return files


def _parse_expect(filepath):
    """Extract the expected error code from the file's header comment."""
    with open(filepath) as f:
        for line in f:
            m = re.match(r"#\s*EXPECT:\s*(E\d+)", line)
            if m:
                return m.group(1)
    return None


def _config_for_file(filepath):
    """Determine the RunConfig based on the corpus file name."""
    basename = os.path.basename(filepath)

    if "capability_denied" in basename:
        # Empty capabilities set = all gated builtins denied
        return RunConfig(capabilities=set())
    elif "host_callback_missing_fs" in basename:
        return RunConfig(capabilities={"fs"})
    elif "host_callback_missing_http" in basename:
        return RunConfig(capabilities={"http"})
    elif "host_callback_missing_process" in basename:
        return RunConfig(capabilities={"process"})
    elif "linked_list_amplification_bomb" in basename:
        return RunConfig(max_collection_size=10, max_steps=10000, timeout=30.0)
    elif "deep_match_bomb" in basename:
        # Match-based recursion uses more Python frames per Geno call
        # (~8 vs ~5), so we lower max_recursion_depth to ensure the Geno
        # recursion check fires before Python's RecursionError.
        return RunConfig(max_recursion_depth=100, max_steps=10000, timeout=30.0)
    elif "step_limit" in basename:
        return RunConfig(max_steps=50, timeout=5.0)
    elif "output_limit" in basename or "output_flood" in basename:
        # Output limit tests need print capability and generous step budget
        # so the output limit fires before the step limit.
        return RunConfig(capabilities={"print"}, max_steps=1_000_000, timeout=30.0)
    elif "bomb" in basename or "exceeded" in basename:
        # Resource exhaustion tests: set explicit step limit so the step
        # counter fires before the wall-clock timeout.
        return RunConfig(max_steps=10000, timeout=30.0)
    else:
        return RunConfig()


CORPUS_FILES = _collect_corpus_files()


@pytest.mark.parametrize(
    "filepath", CORPUS_FILES, ids=[os.path.basename(f) for f in CORPUS_FILES]
)
def test_security_corpus(filepath):
    """Run a security corpus file and verify the expected error code."""
    expected_code_str = _parse_expect(filepath)
    assert expected_code_str is not None, f"No # EXPECT: header in {filepath}"

    # Find the matching ErrorCode enum member
    expected_code = None
    for code in ErrorCode:
        if code.value == expected_code_str:
            expected_code = code
            break
    assert expected_code is not None, f"Unknown error code {expected_code_str}"

    with open(filepath) as f:
        lines = f.readlines()

    # Strip # comment lines (test metadata), Geno uses // for comments
    source = "".join(line for line in lines if not line.lstrip().startswith("#"))

    config = _config_for_file(filepath)
    result = run(source, config=config)

    assert result.ok is False, (
        f"Expected failure with {expected_code_str} but program succeeded "
        f"(value={result.value})"
    )
    matched = [d for d in result.diagnostics if d.code == expected_code]
    assert len(matched) > 0, (
        f"Expected error code {expected_code_str} but got: "
        f"{[d.code.value for d in result.diagnostics]}"
    )
