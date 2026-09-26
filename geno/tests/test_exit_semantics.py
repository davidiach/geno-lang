"""Executable entrypoint exit semantics (docs/spec/v0.5.md 4.1.1).

A declared ``Int`` ``main`` reports its result as the host's exit status,
normalized modulo 256, and stops printing it.  Everything that is *not* a
process boundary keeps handing back the raw value: the embedding API,
``compile_and_exec`` and the generic sandbox are libraries, and a library that
can end its caller's process is a defect rather than a feature.

Backend-level behavior (standalone compiled Python, Node script and Node ESM)
is a separate change and is asserted in ``test_main_result_compatibility.py``
until it lands.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from geno.api import RunConfig, run
from geno.compiler import compile_and_exec
from geno.entrypoint import EntrypointResultKind
from geno.exit_status import exit_status_for_int_result

_MODE_ARGS = [
    pytest.param((), id="process"),
    pytest.param(("--unsafe",), id="direct"),
    pytest.param(("--json",), id="json"),
]


def _run_cli(
    tmp_path: Path, source: str, *mode_args: str, name: str = "App.geno"
) -> subprocess.CompletedProcess[str]:
    app = tmp_path / name
    app.write_text(source, encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "geno",
            "run",
            "--no-check-examples",
            *mode_args,
            str(app),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "status"),
    [
        (0, 0),
        (2, 2),
        (255, 255),
        (256, 0),
        (258, 2),
        (-1, 255),
        (-256, 0),
        (-257, 255),
    ],
)
def test_exit_status_is_mathematical_modulo_256(value: int, status: int) -> None:
    """C-style truncation would leave ``-1`` negative, which no status can be."""
    assert exit_status_for_int_result(value) == status


@pytest.mark.parametrize("mode_args", _MODE_ARGS)
@pytest.mark.parametrize(
    ("returned", "status"),
    [
        pytest.param("258", 2, id="above-255"),
        pytest.param("0 - 1", 255, id="negative"),
    ],
)
def test_every_lane_normalizes_an_out_of_range_result(
    tmp_path: Path, mode_args: tuple[str, ...], returned: str, status: int
) -> None:
    result = _run_cli(
        tmp_path,
        f"func main() -> Int\n    return {returned}\nend func\n",
        *mode_args,
    )

    assert result.returncode == status
    assert result.stderr == ""


@pytest.mark.parametrize("mode_args", [_MODE_ARGS[0], _MODE_ARGS[1]])
def test_a_result_too_wide_to_print_still_reports_its_status(
    tmp_path: Path, mode_args: tuple[str, ...]
) -> None:
    """A valid ``Int`` can be far wider than CPython will render as a string.

    ``max_integer_bits`` admits roughly 10_000 digits, while ``str()`` refuses
    an integer past 4_300, so any lane that renders or serializes the result
    before reducing it dies in transport instead of exiting.  ``2 ** 16000 + 7``
    is 4_817 digits, comfortably either side of both limits.

    ``--json`` is absent deliberately: its envelope reports the raw value, and
    serializing that hits the same limit.  That is a defect in the envelope
    rather than in the exit contract, it predates this change, and fixing it
    means deciding how the envelope should represent such a value.
    """
    result = _run_cli(
        tmp_path,
        "func main() -> Int\n    return 2 ** 16000 + 7\nend func\n",
        *mode_args,
    )

    assert result.returncode == 7, result.stderr
    assert result.stderr == ""
    assert result.stdout == ""


# ---------------------------------------------------------------------------
# The raw value never gets normalized
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("returned", "raw", "status"),
    [
        pytest.param("258", 258, 2, id="above-255"),
        pytest.param("0 - 1", -1, 255, id="negative"),
    ],
)
def test_json_reports_the_raw_value_beside_a_normalized_status(
    tmp_path: Path, returned: str, raw: int, status: int
) -> None:
    """The single easiest mistake here is normalizing the envelope's value too."""
    result = _run_cli(
        tmp_path,
        f"func main() -> Int\n    return {returned}\nend func\n",
        "--json",
    )

    assert result.returncode == status
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["value"] == raw
    assert payload["ok"] is True
    assert payload["diagnostics"] == []


def test_embedding_run_reports_the_raw_value_and_does_not_exit() -> None:
    """``geno.api.run`` is the embedding contract: it may never end the host."""
    result = run(
        "func main() -> Int\n    return 258\nend func\n",
        config=RunConfig(check_examples=False),
    )

    assert result.ok is True
    assert result.value == 258
    assert result.value_raw == 258
    assert result.entrypoint_kind is EntrypointResultKind.INT
    # Reached at all only because the call above returned.
    assert True


def test_compile_and_exec_with_a_timeout_returns_the_raw_value() -> None:
    """A timeout routes through ProcessSandbox, which must stay status-free."""
    globals_dict = compile_and_exec(
        "func main() -> Int\n    return 258\nend func\n",
        typecheck=True,
        sandboxed=True,
        timeout=30.0,
    )

    assert globals_dict["__result__"] == 258
    # The caller keeps running, which is the whole point of the assertion above.
    assert (
        compile_and_exec(
            "func main() -> Int\n    return 1\nend func\n",
            timeout=30.0,
        )["__result__"]
        == 1
    )


def test_compile_and_exec_in_process_leaves_main_to_the_caller() -> None:
    """Without a timeout it execs in place and hands back the full globals.

    It does not run ``main``, so there is no result to mistake for a status;
    calling it returns the raw value and the caller keeps going.
    """
    globals_dict = compile_and_exec(
        "func main() -> Int\n    return 258\nend func\n",
        timeout=None,
    )

    assert "__result__" not in globals_dict
    assert globals_dict["main"]() == 258


# ---------------------------------------------------------------------------
# Classification drives the status, so every form that resolves to Int counts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode_args", _MODE_ARGS)
@pytest.mark.parametrize(
    ("source", "status"),
    [
        pytest.param(
            "type Status = Int\nfunc main() -> Status\n    return 2\nend func\n",
            2,
            id="alias-to-int",
        ),
        pytest.param(
            "async func main() -> Int\n    return 2\nend func\n",
            2,
            id="async-main",
        ),
        pytest.param(
            "async func twice(x: Int) -> Int\n"
            "    return x * 2\n"
            "end func\n"
            "\n"
            "func main() -> Int\n"
            "    return await twice(1)\n"
            "end func\n",
            2,
            id="sync-main-that-awaits",
        ),
    ],
)
def test_a_resolved_int_result_sets_the_status(
    tmp_path: Path, mode_args: tuple[str, ...], source: str, status: int
) -> None:
    result = _run_cli(tmp_path, source, *mode_args)

    assert result.returncode == status, result.stderr
    assert result.stderr == ""


@pytest.mark.parametrize("mode_args", [_MODE_ARGS[0], _MODE_ARGS[1]])
def test_an_unawaited_async_result_is_not_a_status(
    tmp_path: Path, mode_args: tuple[str, ...]
) -> None:
    """A sync ``main`` returning an async value keeps its declared type.

    It is neither ``Int`` nor ``Unit``, so it is displayed and exits 0 rather
    than being awaited on the program's behalf.
    """
    result = _run_cli(
        tmp_path,
        "async func twice(x: Int) -> Int\n"
        "    return x * 2\n"
        "end func\n"
        "\n"
        "func main() -> Async[Int]\n"
        "    return twice(1)\n"
        "end func\n",
        *mode_args,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("=> ")


@pytest.mark.parametrize("mode_args", _MODE_ARGS)
def test_a_missing_main_exits_zero(tmp_path: Path, mode_args: tuple[str, ...]) -> None:
    result = _run_cli(
        tmp_path,
        "func helper() -> Int\n    example () -> 1\n\n    return 1\nend func\n",
        *mode_args,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


@pytest.mark.parametrize("mode_args", _MODE_ARGS)
def test_only_the_entry_module_owns_the_status(
    tmp_path: Path, mode_args: tuple[str, ...]
) -> None:
    """An imported ``main`` is an ordinary function, so it sets no status."""
    (tmp_path / "Lib.geno").write_text(
        "func main() -> Int\n    return 3\nend func\n", encoding="utf-8"
    )
    result = _run_cli(tmp_path, "import Lib\n", *mode_args)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


# ---------------------------------------------------------------------------
# A normal nonzero status is not an error report
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode_args", [_MODE_ARGS[0], _MODE_ARGS[1]])
def test_a_nonzero_status_carries_no_traceback_and_no_result_line(
    tmp_path: Path, mode_args: tuple[str, ...]
) -> None:
    result = _run_cli(
        tmp_path,
        'func main() -> Int\n    print("work done")\n    return 2\nend func\n',
        *mode_args,
    )

    assert result.returncode == 2
    # The result is the status, so printing it as well would show the number
    # the process is about to exit with.
    assert result.stdout == "work done\n"
    assert result.stderr == ""


@pytest.mark.parametrize("mode_args", [_MODE_ARGS[0], _MODE_ARGS[1]])
def test_output_written_before_a_nonzero_result_still_arrives(
    tmp_path: Path, mode_args: tuple[str, ...]
) -> None:
    """Any immediate-termination primitive on this path would drop the buffer."""
    lines = "\n".join(f'    print("line {index}")' for index in range(200))
    result = _run_cli(
        tmp_path,
        f"func main() -> Int\n{lines}\n    return 7\nend func\n",
        *mode_args,
    )

    assert result.returncode == 7
    assert result.stdout.splitlines() == [f"line {index}" for index in range(200)]
    assert result.stderr == ""


def test_an_uncaught_error_still_exits_one_with_a_diagnostic(
    tmp_path: Path,
) -> None:
    """A failure is not a result, so it keeps the error status and the text."""
    result = _run_cli(tmp_path, "func main() -> Int\n    return 1 / 0\nend func\n")

    assert result.returncode == 1
    assert "zero" in result.stderr.lower()


def test_watch_reports_a_nonzero_status_and_keeps_watching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Watch mode is a long-running host: a program's status must not end it.

    ``run_file`` returning the status rather than raising it is what makes this
    work, so this is the regression test for the difference.
    """
    from geno.cli import watch as watch_command

    app = tmp_path / "App.geno"
    app.write_text("func main() -> Int\n    return 7\nend func\n", encoding="utf-8")

    def stop_after_the_first_run(_seconds: float) -> None:
        raise KeyboardInterrupt

    # `watch_run` imports `time` lazily, so the patch goes on the module it
    # reaches. `--unsafe` keeps the run in this process, where nothing else
    # sleeps and the poll below is the only caller.
    monkeypatch.setattr(time, "sleep", stop_after_the_first_run)

    watch_command.watch_run(str(app), unsafe=True)

    captured = capsys.readouterr().out
    assert "Process exited with status 7" in captured
    assert "Watch mode stopped." in captured
