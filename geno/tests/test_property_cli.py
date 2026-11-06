"""``geno test --properties`` on the command line (proposal 0017).

The engine itself is tested as a library in ``test_property_check.py``; these
tests cover the command: the flags and their usage errors, the text and JSON
reports, the exit status, files, manifest-less directories and projects,
``--filter``, the reproduce line, and ``--watch`` pass-through.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from geno.execution_limits import MAX_PROPERTY_TRIALS

REPO_ROOT = Path(__file__).resolve().parents[2]

ABS_BUG = """\
func abs_val(n: Int) -> Int
    ensures result >= 0
    example 3 -> 3
    if n >= 0 then
        return n
    end if
    return n
end func
"""

CLAMP_BUG = """\
func clamp_pos(n: Int) -> Int
    ensures result > 0
    example 5 -> 5
    if n > 100 then
        return 100
    end if
    return n
end func
"""

BELOW_HUNDRED = """\
func below_hundred(n: Int) -> Int
    ensures result < 100
    example 1 -> 1
    return n
end func
"""

DOUBLE_OK = """\
func double(n: Int) -> Int
    ensures result == n + n
    example 2 -> 4
    return n * 2
end func
"""

APPLY_SKIPPED = """\
func apply(f: (Int) -> Int, x: Int) -> Int
    example fn(y: Int) -> y, 1 -> 1
    return f(x)
end func
"""

EXACT_GAVE_UP = """\
func exact(n: Int) -> Int
    requires n == 12345
    example 12345 -> 12345
    return n
end func
"""

COUNT_LOOP = """\
func count(n: Int) -> Int
    ensures result >= 0
    example 3 -> 50
    var i = 0
    while i < 50 do
        i = i + 1
    end while
    return i
end func
"""

# An app-mode file: its helpers need no examples, so the file has none.
APP_WITHOUT_EXAMPLES = """\
type Model = Model(count: Int)

func clamp_count(n: Int) -> Int
    ensures result >= 0
    if n > 10 then
        return 10
    end if
    return n
end func

func init() -> Model
    return Model(0)
end func

func update(model: Model, dt: Float) -> Model
    return Model(clamp_count(model.count + 1))
end func

func render(model: Model) -> Unit
    return ()
end func
"""


def geno_test(*args: str, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess[str]:
    """Run ``geno test ARGS`` in a subprocess from *cwd*."""
    env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    return subprocess.run(
        [sys.executable, "-m", "geno", "test", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def geno_test_json(*args: str, cwd: Path = REPO_ROOT) -> tuple[int, dict[str, Any]]:
    completed = geno_test("--json", *args, cwd=cwd)
    return completed.returncode, json.loads(completed.stdout)


def write(directory: Path, name: str, source: str) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def functions(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The JSON ``properties.functions`` entries, by function name."""
    return {entry["function"]: entry for entry in payload["properties"]["functions"]}


def reproduce_line(stdout: str, function: str) -> list[str]:
    """The reproduce command printed under *function*'s failure, as argv."""
    lines = stdout.splitlines()
    start = next(
        index for index, line in enumerate(lines) if line.split()[1:2] == [function]
    )
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("reproduce: "):
            return shlex.split(stripped[len("reproduce: ") :])
    raise AssertionError(f"no reproduce line for {function}:\n{stdout}")


# =============================================================================
# The text report
# =============================================================================


class TestTextReport:
    def test_abs_val_failure_names_seed_counterexample_and_reproduce_line(
        self, tmp_path
    ):
        write(tmp_path, "abs.geno", ABS_BUG)

        completed = geno_test("--properties", "abs.geno", cwd=tmp_path)

        assert completed.returncode == 1, completed.stdout + completed.stderr
        lines = completed.stdout.splitlines()
        assert lines[0].startswith("  PASS  abs.geno (1/1)")
        header = lines.index("Properties (seed 0, 100 trials per function)")
        assert lines[header + 1] == "  FAIL  abs_val  abs.geno:1"
        assert lines[header + 2] == "        ensures result >= 0 failed (result was -1)"
        assert lines[header + 3].startswith("        counterexample: -1")
        assert lines[header + 4] == (
            "        reproduce: geno test --properties --seed 0 --filter abs_val "
            "abs.geno"
        )
        summary = next(
            index for index, line in enumerate(lines) if line.startswith("1 passed, ")
        )
        # The example report, then the property block, then both summaries.
        assert 0 < header < summary
        assert lines[summary + 1] == "Properties: 0 passed, 1 failed"
        assert lines[-1] == "Properties: 0 passed, 1 failed"

    def test_a_shrunk_counterexample_says_what_it_was_shrunk_from(self, tmp_path):
        write(tmp_path, "bound.geno", BELOW_HUNDRED)

        status, payload = geno_test_json("--properties", "bound.geno", cwd=tmp_path)
        completed = geno_test("--properties", "bound.geno", cwd=tmp_path)

        assert status == completed.returncode == 1
        shrunk = functions(payload)["below_hundred"]["counterexample"]
        # Only values of at least 100 fail, so a generated one shrinks to 100.
        assert shrunk["arguments"] == "100"
        assert shrunk["original"] != "100" and shrunk["shrink_steps"] > 1
        assert "        ensures result < 100 failed (result was 100)" in (
            completed.stdout
        )
        assert (
            f"        counterexample: 100  (shrunk from {shrunk['original']} in "
            f"{shrunk['shrink_steps']} steps)"
        ) in completed.stdout.splitlines()

    def test_passed_and_skipped_functions_are_listed_only_with_verbose(self, tmp_path):
        write(tmp_path, "mixed.geno", DOUBLE_OK + "\n" + APPLY_SKIPPED)

        quiet = geno_test("--properties", "mixed.geno", cwd=tmp_path)
        verbose = geno_test("--properties", "-v", "mixed.geno", cwd=tmp_path)

        assert quiet.returncode == 0
        assert "  PASS  double" not in quiet.stdout
        assert "  SKIP  apply" not in quiet.stdout
        assert "  no failures or warnings" in quiet.stdout
        assert "Properties: 1 passed, 0 failed, 1 skipped" in quiet.stdout

        assert verbose.returncode == 0
        lines = verbose.stdout.splitlines()
        assert "  PASS  double  mixed.geno:1  (100 trials)" in lines
        skip = lines.index("  SKIP  apply  mixed.geno:7")
        assert lines[skip + 1] == "        parameter f: (Int) -> Int has no generator"
        assert "no failures or warnings" not in verbose.stdout

    def test_settings_appear_in_the_header_and_the_reproduce_line(self, tmp_path):
        write(tmp_path, "abs.geno", ABS_BUG)

        completed = geno_test(
            "--properties",
            "--trials",
            "40",
            "--seed",
            "3",
            "--max-steps",
            "1000000",
            "--timeout",
            "4",
            "abs.geno",
            cwd=tmp_path,
        )

        assert completed.returncode == 1
        assert "Properties (seed 3, 40 trials per function)" in completed.stdout
        assert reproduce_line(completed.stdout, "abs_val") == [
            "geno",
            "test",
            "--properties",
            "--seed",
            "3",
            "--trials",
            "40",
            "--timeout",
            "4",
            "--max-steps",
            "1000000",
            "--filter",
            "abs_val",
            "abs.geno",
        ]

    def test_a_path_with_spaces_is_quoted_in_the_reproduce_line(self, tmp_path):
        write(tmp_path, "my dir/abs.geno", ABS_BUG)

        completed = geno_test("--properties", "my dir/abs.geno", cwd=tmp_path)

        assert completed.returncode == 1
        assert "--filter abs_val 'my dir/abs.geno'" in completed.stdout
        assert reproduce_line(completed.stdout, "abs_val")[-1] == "my dir/abs.geno"

    def test_warnings_are_listed_with_their_notes(self, tmp_path):
        write(tmp_path, "exact.geno", EXACT_GAVE_UP)

        completed = geno_test("--properties", "exact.geno", cwd=tmp_path)

        assert completed.returncode == 0, completed.stdout
        lines = completed.stdout.splitlines()
        entry = lines.index("  GAVE UP  exact  exact.geno:1")
        assert lines[entry + 1].strip().startswith("gave up: only 0 of 1000")
        assert "reproduce:" not in completed.stdout
        assert "Properties: 0 passed, 0 failed, 1 gave up" in completed.stdout

    def test_a_file_that_cannot_be_checked_reports_no_functions(self, tmp_path):
        write(tmp_path, "broken.geno", "func broken(n: Int) -> Int\n")

        completed = geno_test("--properties", "broken.geno", cwd=tmp_path)

        assert completed.returncode == 1
        assert "  ERROR  broken.geno" in completed.stdout
        assert "  no functions to check" in completed.stdout
        assert "Properties: 0 passed, 0 failed" in completed.stdout


# =============================================================================
# JSON
# =============================================================================

_PROPERTY_KEYS = [
    "seed",
    "trials",
    "passed",
    "failed",
    "gave_up",
    "incomplete",
    "skipped",
    "functions",
]
_FUNCTION_KEYS = [
    "path",
    "function",
    "line",
    "status",
    "trials_run",
    "discarded",
    "failure",
    "counterexample",
    "note",
]


class TestJson:
    def test_the_properties_section_has_a_stable_shape(self, tmp_path):
        write(tmp_path, "mixed.geno", ABS_BUG + "\n" + DOUBLE_OK + "\n" + APPLY_SKIPPED)

        status, payload = geno_test_json("--properties", "mixed.geno", cwd=tmp_path)

        assert status == 1
        assert list(payload) == [
            "files",
            "total",
            "passed",
            "failed",
            "errors",
            "untested",
            "success",
            "properties",
        ]
        # Property results are not example counts; a failure still fails.
        assert (payload["total"], payload["passed"], payload["failed"]) == (3, 3, 0)
        assert payload["success"] is False

        properties = payload["properties"]
        assert list(properties) == _PROPERTY_KEYS
        assert properties["seed"] == 0 and properties["trials"] == 100
        assert (
            properties["passed"],
            properties["failed"],
            properties["gave_up"],
            properties["incomplete"],
            properties["skipped"],
        ) == (1, 1, 0, 0, 1)

        by_name = functions(payload)
        assert list(by_name) == ["abs_val", "double", "apply"]
        for entry in by_name.values():
            assert list(entry) == _FUNCTION_KEYS
            assert entry["path"] == "mixed.geno"

        failed = by_name["abs_val"]
        assert failed["status"] == "failed" and failed["line"] == 1
        assert list(failed["failure"]) == [
            "kind",
            "message",
            "clause",
            "result",
            "limit",
        ]
        assert failed["failure"]["kind"] == "ensures"
        assert failed["failure"]["clause"] == "result >= 0"
        assert failed["failure"]["result"] == "-1"
        assert failed["failure"]["limit"] is None
        assert list(failed["counterexample"]) == [
            "arguments",
            "original",
            "shrink_steps",
        ]
        assert failed["counterexample"]["arguments"] == "-1"

        passed = by_name["double"]
        assert passed["status"] == "passed"
        assert passed["trials_run"] == 100
        assert passed["failure"] is None and passed["counterexample"] is None

        skipped = by_name["apply"]
        assert skipped["status"] == "skipped"
        assert skipped["note"] == "parameter f: (Int) -> Int has no generator"

    def test_the_same_run_gives_the_same_json(self, tmp_path):
        write(tmp_path, "abs.geno", ABS_BUG + "\n" + CLAMP_BUG)

        first = geno_test_json("--properties", "--seed", "8", "abs.geno", cwd=tmp_path)
        second = geno_test_json("--properties", "--seed", "8", "abs.geno", cwd=tmp_path)

        assert first[1]["properties"] == second[1]["properties"]

    def test_the_error_envelope_carries_an_empty_properties_section(self, tmp_path):
        status, payload = geno_test_json(
            "--properties", "--seed", "4", str(tmp_path / "missing.geno")
        )

        assert status == 1
        assert payload["errors"] == 1 and payload["success"] is False
        assert "not found" in payload["files"][0]["error"]
        assert payload["properties"] == {
            "seed": 4,
            "trials": 100,
            "passed": 0,
            "failed": 0,
            "gave_up": 0,
            "incomplete": 0,
            "skipped": 0,
            "functions": [],
        }

    def test_a_project_error_keeps_the_properties_section(self, tmp_path):
        write(tmp_path, "geno.toml", 'entrypoint = "App"\nfiles = ["App"]\n')
        write(tmp_path, "App.geno", "func f(n: Int) -> Int\n    return n\nend func\n")

        status, payload = geno_test_json("--properties", str(tmp_path))

        assert status == 1
        assert payload["errors"] == 1
        assert "Type Error" in payload["files"][0]["error"]
        assert payload["properties"]["functions"] == []

    def test_without_properties_there_is_no_properties_key(self, tmp_path):
        write(tmp_path, "abs.geno", ABS_BUG)

        status, payload = geno_test_json("abs.geno", cwd=tmp_path)

        assert status == 0
        assert "properties" not in payload
        assert payload["success"] is True


# =============================================================================
# Exit status
# =============================================================================


class TestExitStatus:
    def test_a_property_failure_exits_one(self, tmp_path):
        write(tmp_path, "abs.geno", ABS_BUG)
        assert geno_test("--properties", "abs.geno", cwd=tmp_path).returncode == 1

    def test_plain_geno_test_still_passes_the_same_file(self, tmp_path):
        write(tmp_path, "abs.geno", ABS_BUG)

        completed = geno_test("abs.geno", cwd=tmp_path)

        assert completed.returncode == 0
        assert "Properties" not in completed.stdout

    @pytest.mark.parametrize(
        ("source", "status"),
        [(EXACT_GAVE_UP, "gave_up"), (APPLY_SKIPPED, "skipped")],
    )
    def test_warnings_exit_zero(self, tmp_path, source, status):
        write(tmp_path, "warn.geno", source)

        code, payload = geno_test_json("--properties", "warn.geno", cwd=tmp_path)

        assert code == 0
        assert payload["success"] is True
        assert [entry["status"] for entry in payload["properties"]["functions"]] == [
            status
        ]

    def test_incomplete_exits_zero(self, tmp_path, monkeypatch, capsys):
        """A spent step budget is a warning; it is forced small in-process."""
        import geno.cli.test as cli_test
        from geno.__main__ import main

        original = cli_test._property_config

        def small_budget(**settings: Any):
            config = original(**settings)
            return dataclasses.replace(config, trial_step_budget=5_000)

        monkeypatch.setattr(cli_test, "_property_config", small_budget)
        path = write(tmp_path, "count.geno", COUNT_LOOP)

        with pytest.raises(SystemExit) as exit_info:
            main(["test", "--properties", "--json", str(path)])

        assert exit_info.value.code == 0
        payload = json.loads(capsys.readouterr().out)
        (entry,) = payload["properties"]["functions"]
        assert entry["status"] == "incomplete"
        assert "step budget spent" in entry["note"]
        assert payload["success"] is True


# =============================================================================
# Flags and usage errors
# =============================================================================


class TestUsage:
    @pytest.mark.parametrize("flag", ["--trials", "--seed"])
    def test_trials_or_seed_without_properties_is_a_usage_error(self, tmp_path, flag):
        path = write(tmp_path, "abs.geno", ABS_BUG)

        completed = geno_test(flag, "5", str(path))

        assert completed.returncode == 2
        assert f"argument {flag}: requires --properties" in completed.stderr
        assert completed.stdout == ""

    @pytest.mark.parametrize("value", ["0", str(MAX_PROPERTY_TRIALS + 1), "x", "-1"])
    def test_trials_out_of_range_is_a_usage_error(self, tmp_path, value):
        path = write(tmp_path, "abs.geno", ABS_BUG)

        completed = geno_test("--properties", f"--trials={value}", str(path))

        assert completed.returncode == 2
        assert f"must be an integer from 1 to {MAX_PROPERTY_TRIALS}" in completed.stderr

    def test_the_trial_range_bounds_are_accepted(self, tmp_path):
        write(tmp_path, "abs.geno", ABS_BUG)

        for value in ("1", str(MAX_PROPERTY_TRIALS)):
            # abs_val fails within the first trials, so the upper bound is fast.
            status, payload = geno_test_json(
                "--properties",
                "--trials",
                value,
                "--seed",
                "0",
                "abs.geno",
                cwd=tmp_path,
            )
            assert payload["properties"]["trials"] == int(value)
            assert status in (0, 1)

    def test_a_negative_seed_is_a_usage_error(self, tmp_path):
        path = write(tmp_path, "abs.geno", ABS_BUG)

        completed = geno_test("--properties", "--seed=-1", str(path))

        assert completed.returncode == 2
        assert "must be a non-negative integer" in completed.stderr


# =============================================================================
# What is checked: --filter, files, directories, projects
# =============================================================================


class TestSelection:
    def test_filter_selects_examples_and_properties_alike(self, tmp_path):
        write(tmp_path, "two.geno", ABS_BUG + "\n" + CLAMP_BUG)

        status, payload = geno_test_json(
            "--properties", "--filter", "clamp_pos", "two.geno", cwd=tmp_path
        )

        assert status == 1
        assert payload["total"] == 1
        assert list(functions(payload)) == ["clamp_pos"]

    def test_filtering_does_not_change_a_functions_inputs(self, tmp_path):
        write(tmp_path, "two.geno", ABS_BUG + "\n" + CLAMP_BUG)

        _, everything = geno_test_json(
            "--properties", "--seed", "6", "two.geno", cwd=tmp_path
        )
        _, filtered = geno_test_json(
            "--properties",
            "--seed",
            "6",
            "--filter",
            "clamp_pos",
            "two.geno",
            cwd=tmp_path,
        )

        assert functions(filtered)["clamp_pos"] == functions(everything)["clamp_pos"]

    def test_a_manifest_less_directory_checks_every_file(self, tmp_path):
        write(tmp_path, "pkg/abs.geno", ABS_BUG)
        write(tmp_path, "pkg/nested/double.geno", DOUBLE_OK)

        status, payload = geno_test_json("--properties", "pkg", cwd=tmp_path)

        assert status == 1
        by_name = functions(payload)
        assert by_name["abs_val"]["path"] == str(Path("pkg") / "abs.geno")
        assert by_name["abs_val"]["status"] == "failed"
        assert by_name["double"]["path"] == str(Path("pkg") / "nested" / "double.geno")
        assert by_name["double"]["status"] == "passed"

    def test_a_project_checks_its_own_modules_with_their_checkers(self, tmp_path):
        write(tmp_path, "geno.toml", 'entrypoint = "App"\nfiles = ["App", "Util"]\n')
        write(
            tmp_path,
            "App.geno",
            "import List\n"
            "import Util\n"
            "\n"
            "type Pair = Pair(a: Int, b: Int)\n"
            "\n"
            "func first(p: Pair) -> Int\n"
            "    example Pair(1, 2) -> 1\n"
            "    return p.a\n"
            "end func\n"
            "\n"
            '@untested("entry point")\n'
            "func main() -> Int\n"
            "    return twice(1)\n"
            "end func\n",
        )
        write(
            tmp_path,
            "Util.geno",
            "func twice(n: Int) -> Int\n"
            "    ensures result >= n\n"
            "    example 1 -> 2\n"
            "    return n * 2\n"
            "end func\n",
        )

        status, payload = geno_test_json("--properties", ".", cwd=tmp_path)

        assert status == 1
        by_name = functions(payload)
        # The standard library's examples ran; its properties are not checked.
        assert any(entry["path"].endswith("List.geno") for entry in payload["files"])
        assert set(by_name) == {"twice", "first"}
        assert by_name["twice"]["status"] == "failed"
        assert by_name["twice"]["counterexample"]["arguments"] == "-1"
        assert Path(by_name["twice"]["path"]).name == "Util.geno"
        assert by_name["first"]["status"] == "passed"

    def test_a_file_with_no_examples_is_still_checked(self, tmp_path):
        write(tmp_path, "app.geno", APP_WITHOUT_EXAMPLES)

        plain = geno_test_json("app.geno", cwd=tmp_path)
        status, payload = geno_test_json("--properties", "app.geno", cwd=tmp_path)

        assert plain[0] == 0 and plain[1]["total"] == 0
        assert status == 1
        assert payload["total"] == 0
        by_name = functions(payload)
        # The app lifecycle hooks are out of scope; the helper is checked.
        assert list(by_name) == ["clamp_count"]
        assert by_name["clamp_count"]["counterexample"]["arguments"] == "-1"


# =============================================================================
# Reproducing a failure
# =============================================================================


class TestReproduce:
    def test_the_reproduce_command_reproduces_from_any_cwd(self, tmp_path):
        source = write(tmp_path, "work/shapes.geno", ABS_BUG + "\n" + CLAMP_BUG)
        first = geno_test(
            "--properties", "--seed", "21", "shapes.geno", cwd=source.parent
        )
        assert first.returncode == 1
        _, original = geno_test_json(
            "--properties", "--seed", "21", "shapes.geno", cwd=source.parent
        )
        expected = functions(original)["clamp_pos"]

        argv = reproduce_line(first.stdout, "clamp_pos")
        assert argv[:3] == ["geno", "test", "--properties"]
        assert argv[-1] == "shapes.geno"

        # Verbatim, from the same directory.
        status, same_cwd = geno_test_json(*argv[2:], cwd=source.parent)
        # From elsewhere, with another spelling of the same file.
        status_elsewhere, elsewhere = geno_test_json(
            *argv[2:-1], str(source.resolve()), cwd=tmp_path
        )

        assert status == status_elsewhere == 1
        for payload in (same_cwd, elsewhere):
            (entry,) = payload["properties"]["functions"]
            assert entry["function"] == "clamp_pos"
            assert entry["failure"] == expected["failure"]
            assert entry["counterexample"] == expected["counterexample"]
            assert entry["trials_run"] == expected["trials_run"]


# =============================================================================
# --watch
# =============================================================================


class TestWatch:
    def test_watch_receives_the_property_settings(self, tmp_path, monkeypatch):
        import geno.cli.test as cli_test
        from geno.__main__ import main

        path = write(tmp_path, "abs.geno", ABS_BUG)
        calls: list[dict[str, Any]] = []
        monkeypatch.setattr(
            cli_test, "_run_tests_watch", lambda *args, **kwargs: calls.append(kwargs)
        )

        main(
            [
                "test",
                "--watch",
                "--properties",
                "--trials",
                "7",
                "--seed",
                "9",
                str(path),
            ]
        )

        (kwargs,) = calls
        config = kwargs["properties"]
        assert (config.trials, config.seed) == (7, 9)

    def test_watch_without_properties_passes_none(self, tmp_path, monkeypatch):
        import geno.cli.test as cli_test
        from geno.__main__ import main

        path = write(tmp_path, "abs.geno", ABS_BUG)
        calls: list[dict[str, Any]] = []
        monkeypatch.setattr(
            cli_test, "_run_tests_watch", lambda *args, **kwargs: calls.append(kwargs)
        )

        main(["test", "--watch", str(path)])

        assert calls[0]["properties"] is None

    def test_a_watch_run_prints_the_property_block(self, tmp_path, monkeypatch, capsys):
        from geno.cli.test import _property_config, _run_tests_watch

        path = write(tmp_path, "abs.geno", ABS_BUG)

        def stop(_seconds: float) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(time, "sleep", stop)
        _run_tests_watch(
            str(path),
            properties=_property_config(
                trials=None, seed=None, timeout=5.0, max_steps=1_000_000
            ),
        )

        out = capsys.readouterr().out
        assert "Properties (seed 0, 100 trials per function)" in out
        assert "  FAIL  abs_val" in out
        assert "Watch mode stopped." in out

    def test_geno_watch_test_mode_forwards_property_settings(
        self, tmp_path, monkeypatch
    ):
        import geno.cli.test as cli_test
        from geno.cli.watch import watch_run

        calls: list[dict[str, Any]] = []
        monkeypatch.setattr(
            cli_test, "_run_tests_watch", lambda *args, **kwargs: calls.append(kwargs)
        )
        config = cli_test._property_config(
            trials=3, seed=2, timeout=5.0, max_steps=1_000_000
        )

        watch_run(str(tmp_path), test_mode=True, properties=config)

        assert calls[0]["properties"] is config
