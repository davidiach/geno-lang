"""
Security Regression Corpus Tests
==================================

Parametrized tests that run each .geno file in the security_corpus/ directory
and verify the expected error code from the ``# EXPECT: Exxx`` header.

Entries under ``security_corpus/isolated/`` run through a real process-isolated
``geno run`` instead (proposal 0013). Besides ``# EXPECT`` they carry
``# LANE: isolated``, ``# RUN:`` (extra ``geno run`` arguments) and
``# STDERR:`` (a substring the failure must report); stdout must never contain
``ESCAPED``.
"""

import os
import re
import shlex
import subprocess
import sys

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
        # counter fires before the wall-clock timeout, and the hosted
        # server's call depth so recursion bombs meet the depth guard first.
        return RunConfig(max_steps=10000, max_recursion_depth=500, timeout=30.0)
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


ISOLATED_CORPUS_DIR = os.path.join(CORPUS_DIR, "isolated")

# The text a failure of each expected category must contain on stderr.
ISOLATED_CATEGORY_MARKERS = {
    "E412": ("Capability denied", "host env policy", "process-isolated run mode"),
    "E414": ("Runtime Error:",),
}

# Host policy the caller's environment could set for a run. Entries run under
# the default policy, so a developer's own settings cannot weaken one.
ISOLATED_HOST_POLICY_VARIABLES = frozenset(
    {
        "GENO_FS_ALLOW_ABSOLUTE",
        "GENO_FS_READ_ONLY",
        "GENO_FS_ROOTS",
        "GENO_HTTP_ALLOW_PRIVATE",
    }
)


# Host internals an isolated run's report must never show.
ISOLATED_HOST_FRAME_MARKERS = (
    "<string>",
    "<geno-runtime-prelude>",
    "sandbox.py",
    "isolated_host.py",
    "_runtime_support",
    "safe_print",
)


def _collect_isolated_corpus_files():
    return [
        os.path.join(ISOLATED_CORPUS_DIR, name)
        for name in sorted(os.listdir(ISOLATED_CORPUS_DIR))
        if name.endswith(".geno")
    ]


def _parse_isolated_headers(filepath):
    """Return the ``# KEY: value`` headers of an isolated-lane entry."""
    headers = {}
    with open(filepath, encoding="utf-8") as f:
        for line in f:
            m = re.match(r"#\s*(EXPECT|LANE|RUN|STDERR):\s*(.*?)\s*$", line)
            if m:
                headers[m.group(1)] = m.group(2)
    return headers


ISOLATED_CORPUS_FILES = _collect_isolated_corpus_files()


@pytest.mark.parametrize(
    "filepath",
    ISOLATED_CORPUS_FILES,
    ids=[os.path.basename(f) for f in ISOLATED_CORPUS_FILES],
)
def test_isolated_security_corpus(filepath, tmp_path):
    """Run an isolated-lane corpus entry through a real ``geno run``."""
    headers = _parse_isolated_headers(filepath)
    for key in ("EXPECT", "LANE", "RUN", "STDERR"):
        assert key in headers, f"No # {key}: header in {filepath}"
    assert headers["LANE"] == "isolated"
    markers = ISOLATED_CATEGORY_MARKERS.get(headers["EXPECT"])
    assert markers is not None, f"Unsupported code {headers['EXPECT']}"

    with open(filepath, encoding="utf-8") as f:
        lines = f.readlines()
    source = "".join(line for line in lines if not line.lstrip().startswith("#"))
    (tmp_path / "main.geno").write_text(source, encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "geno",
            "run",
            *shlex.split(headers["RUN"]),
            "main.geno",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
        env={
            key: value
            for key, value in os.environ.items()
            if key not in ISOLATED_HOST_POLICY_VARIABLES
        },
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "ESCAPED" not in result.stdout
    assert headers["STDERR"] in result.stderr
    assert any(marker in result.stderr for marker in markers), result.stderr
    assert "Traceback (most recent call last)" not in result.stderr
    # A runtime report names only the program's own file (proposal 0018):
    # no worker, sandbox, isolated-host or prelude frame and no host path.
    for host_marker in ISOLATED_HOST_FRAME_MARKERS:
        assert host_marker not in result.stderr, result.stderr
    program = str(tmp_path / "main.geno")
    for line in result.stderr.splitlines():
        frame = re.match(r"^  (?:at )?(.+):\d+:\d+(?: in .+)?$", line)
        if frame is not None:
            assert frame.group(1) == program, line
