#!/usr/bin/env python3
"""Run a frozen Geno language conformance corpus across supported backends."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence, cast

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised by the supported Python 3.10 CI job
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
CONFORMANCE_ROOT = ROOT / "conformance"
SPEC_PATH = ROOT / "spec.json"
FIRST_FROZEN_SERIES = (0, 4)
DEFAULT_MANIFEST = CONFORMANCE_ROOT / "v0.4" / "manifest.toml"
RUNTIME_TARGETS = ("interpreter", "python", "js")
ALL_TARGETS = ("checker", *RUNTIME_TARGETS)

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class ManifestError(ValueError):
    """Raised when a conformance manifest is malformed or unsafe."""


def _parse_series(raw: Any, *, field: str) -> tuple[int, int]:
    if not isinstance(raw, str):
        raise ManifestError(f"{field} must be a major.minor string")
    parts = raw.split(".")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ManifestError(f"{field} must be a major.minor string")
    return int(parts[0]), int(parts[1])


def retained_manifest_paths(
    *,
    spec_path: Path = SPEC_PATH,
    conformance_root: Path = CONFORMANCE_ROOT,
) -> tuple[Path, ...]:
    """Return the current and immediately preceding frozen language corpora."""
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(
            f"cannot load language contract {spec_path}: {exc}"
        ) from exc
    current = _parse_series(spec.get("language_series"), field="language_series")

    available: dict[tuple[int, int], Path] = {}
    for manifest_path in conformance_root.glob("v*/manifest.toml"):
        raw_series = manifest_path.parent.name.removeprefix("v")
        try:
            series = _parse_series(raw_series, field=str(manifest_path.parent))
        except ManifestError:
            continue
        available[series] = manifest_path.resolve()

    if current not in available:
        raise ManifestError(
            f"missing current conformance corpus: v{current[0]}.{current[1]}"
        )

    selected = [current]
    if current != FIRST_FROZEN_SERIES:
        if current[1] > 0:
            previous = (current[0], current[1] - 1)
            if previous not in available:
                raise ManifestError(
                    "missing immediately preceding minor conformance corpus: "
                    f"v{previous[0]}.{previous[1]}"
                )
        else:
            prior = sorted(series for series in available if series < current)
            if not prior:
                raise ManifestError(
                    "missing retained previous-minor conformance corpus"
                )
            previous = prior[-1]
        selected.insert(0, previous)

    return tuple(available[series] for series in selected)


@dataclass(frozen=True)
class ConformanceCase:
    """One immutable source or diagnostic compatibility contract."""

    id: str
    path: Path
    kind: str
    targets: tuple[str, ...]
    capabilities: frozenset[str]
    expected_stdout: str | None
    expected_diagnostics: tuple[str, ...]


@dataclass(frozen=True)
class ConformanceManifest:
    """Validated conformance manifest metadata and cases."""

    schema_version: int
    language_version: str
    description: str
    path: Path
    cases: tuple[ConformanceCase, ...]


@dataclass(frozen=True)
class CaseResult:
    """Outcome for one case/target pair."""

    case_id: str
    target: str
    status: str
    detail: str = ""


def _string_list(raw: Any, *, field: str, case_id: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ManifestError(f"{case_id}: {field} must be a list of strings")
    return tuple(raw)


def load_manifest(path: Path = DEFAULT_MANIFEST) -> ConformanceManifest:
    """Load and strictly validate a versioned conformance manifest."""

    resolved_manifest = path.resolve()
    try:
        with resolved_manifest.open("rb") as handle:
            raw = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ManifestError(f"cannot load {resolved_manifest}: {exc}") from exc

    if raw.get("schema_version") != 1:
        raise ManifestError("schema_version must be 1")
    language_version = raw.get("language_version")
    description = raw.get("description")
    if not isinstance(language_version, str) or not language_version:
        raise ManifestError("language_version must be a non-empty string")
    directory_version = resolved_manifest.parent.name.removeprefix("v")
    if language_version != directory_version:
        raise ManifestError(
            f"language_version {language_version!r} does not match "
            f"corpus directory {resolved_manifest.parent.name!r}"
        )
    _parse_series(language_version, field="language_version")
    if not isinstance(description, str) or not description:
        raise ManifestError("description must be a non-empty string")

    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ManifestError("manifest must contain at least one [[cases]] entry")

    manifest_dir = resolved_manifest.parent
    seen_ids: set[str] = set()
    cases: list[ConformanceCase] = []
    for index, raw_case in enumerate(raw_cases, start=1):
        if not isinstance(raw_case, dict):
            raise ManifestError(f"case {index} must be a table")
        case_id = raw_case.get("id")
        if not isinstance(case_id, str) or not case_id:
            raise ManifestError(f"case {index}: id must be a non-empty string")
        if case_id in seen_ids:
            raise ManifestError(f"duplicate case id: {case_id}")
        seen_ids.add(case_id)

        relative_path = raw_case.get("path")
        if not isinstance(relative_path, str) or not relative_path:
            raise ManifestError(f"{case_id}: path must be a non-empty string")
        source_path = (manifest_dir / relative_path).resolve()
        if not source_path.is_relative_to(manifest_dir):
            raise ManifestError(f"{case_id}: path escapes the manifest directory")
        if source_path.suffix != ".geno" or not source_path.is_file():
            raise ManifestError(
                f"{case_id}: source file does not exist: {relative_path}"
            )

        kind = raw_case.get("kind")
        if kind not in {"run", "diagnostic"}:
            raise ManifestError(f"{case_id}: kind must be 'run' or 'diagnostic'")
        targets = _string_list(
            raw_case.get("targets"), field="targets", case_id=case_id
        )
        if not targets or len(set(targets)) != len(targets):
            raise ManifestError(f"{case_id}: targets must be non-empty and unique")
        unknown_targets = set(targets) - set(ALL_TARGETS)
        if unknown_targets:
            raise ManifestError(
                f"{case_id}: unknown targets: {', '.join(sorted(unknown_targets))}"
            )

        capabilities = frozenset(
            _string_list(
                raw_case.get("capabilities", []),
                field="capabilities",
                case_id=case_id,
            )
        )
        expected_stdout = raw_case.get("expected_stdout")
        expected_diagnostics = _string_list(
            raw_case.get("expected_diagnostics", []),
            field="expected_diagnostics",
            case_id=case_id,
        )
        if kind == "run":
            if not isinstance(expected_stdout, str):
                raise ManifestError(f"{case_id}: run case requires expected_stdout")
            if "checker" in targets or expected_diagnostics:
                raise ManifestError(f"{case_id}: run case has diagnostic-only fields")
        else:
            if targets != ("checker",):
                raise ManifestError(f"{case_id}: diagnostic case must target checker")
            if not expected_diagnostics or expected_stdout is not None or capabilities:
                raise ManifestError(
                    f"{case_id}: diagnostic case requires only expected_diagnostics"
                )

        cases.append(
            ConformanceCase(
                id=case_id,
                path=source_path,
                kind=kind,
                targets=targets,
                capabilities=capabilities,
                expected_stdout=expected_stdout,
                expected_diagnostics=expected_diagnostics,
            )
        )

    return ConformanceManifest(
        schema_version=1,
        language_version=language_version,
        description=description,
        path=resolved_manifest,
        cases=tuple(cases),
    )


def _capability_args(capabilities: Iterable[str]) -> list[str]:
    values = sorted(capabilities)
    return ["--cap", ",".join(values)] if values else []


def _run_interpreter(case: ConformanceCase, source: str, timeout: float) -> str:
    from geno.api import RunConfig, run

    result = run(
        source,
        filename=str(case.path),
        config=RunConfig(
            timeout=timeout,
            max_steps=1_000_000,
            capabilities=set(case.capabilities),
        ),
    )
    if not result.ok:
        diagnostics = "; ".join(
            f"{diagnostic.code.value}: {diagnostic.message}"
            for diagnostic in result.diagnostics
        )
        raise RuntimeError(diagnostics or "interpreter failed without diagnostics")
    return cast(str, result.output)


def _run_python(case: ConformanceCase, source: str, timeout: float) -> str:
    from geno.compiler import compile_to_python

    generated = compile_to_python(source)
    with tempfile.TemporaryDirectory(prefix="geno-conformance-python-") as raw_dir:
        artifact = Path(raw_dir) / "case.py"
        artifact.write_text(generated, encoding="utf-8", newline="\n")
        # The executable and generated artifact are controlled by this runner;
        # manifest source is type-checked immediately before compilation.
        completed = subprocess.run(  # noqa: S603
            [sys.executable, str(artifact), *_capability_args(case.capabilities)],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    if completed.returncode != 0:
        raise RuntimeError(
            f"compiled Python exited {completed.returncode}: {completed.stderr.strip()}"
        )
    return completed.stdout


def _run_js(case: ConformanceCase, source: str, timeout: float) -> str:
    from geno.js_compiler import compile_to_js

    node_path = shutil.which("node")
    if node_path is None:
        raise RuntimeError("Node.js is not available")
    generated = compile_to_js(source)
    if isinstance(generated, tuple):
        generated = generated[0]
    with tempfile.TemporaryDirectory(prefix="geno-conformance-js-") as raw_dir:
        artifact = Path(raw_dir) / "case.js"
        artifact.write_text(generated, encoding="utf-8", newline="\n")
        # node_path is resolved from PATH; the artifact is generated by Geno.
        completed = subprocess.run(  # noqa: S603
            [node_path, str(artifact), *_capability_args(case.capabilities)],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    if completed.returncode != 0:
        raise RuntimeError(
            f"compiled JavaScript exited {completed.returncode}: {completed.stderr.strip()}"
        )
    return completed.stdout


def _check_case(case: ConformanceCase, source: str) -> CaseResult:
    from geno.api import check

    result = check(source, filename=str(case.path))
    actual_codes = tuple(diagnostic.code.value for diagnostic in result.diagnostics)
    if case.kind == "diagnostic":
        if actual_codes == case.expected_diagnostics:
            return CaseResult(case.id, "checker", "passed")
        return CaseResult(
            case.id,
            "checker",
            "failed",
            f"expected diagnostics {case.expected_diagnostics!r}, got {actual_codes!r}",
        )
    if result.ok:
        return CaseResult(case.id, "checker", "passed")
    detail = "; ".join(
        f"{diagnostic.code.value}: {diagnostic.message}"
        for diagnostic in result.diagnostics
    )
    return CaseResult(case.id, "checker", "failed", detail)


def run_suite(
    manifest: ConformanceManifest,
    *,
    target: str = "all",
    case_ids: frozenset[str] | None = None,
    timeout: float = 10.0,
    require_node: bool = False,
) -> list[CaseResult]:
    """Run selected cases, returning every checker/backend outcome."""

    if target not in {"all", *ALL_TARGETS}:
        raise ValueError(f"unknown target: {target}")
    known_ids = {case.id for case in manifest.cases}
    if case_ids:
        unknown_ids = case_ids - known_ids
        if unknown_ids:
            raise ValueError(f"unknown case ids: {', '.join(sorted(unknown_ids))}")

    node_available = shutil.which("node") is not None
    results: list[CaseResult] = []
    runners = {
        "interpreter": _run_interpreter,
        "python": _run_python,
        "js": _run_js,
    }
    for case in manifest.cases:
        if case_ids and case.id not in case_ids:
            continue
        source = case.path.read_text(encoding="utf-8")
        check_result = _check_case(case, source)
        if target in {"all", "checker"}:
            results.append(check_result)
        if case.kind == "diagnostic" or target == "checker":
            continue
        if check_result.status != "passed":
            if target not in {"all", "checker"}:
                results.append(check_result)
            continue

        selected_targets: Sequence[str]
        if target == "all":
            selected_targets = case.targets
        elif target in case.targets:
            selected_targets = (target,)
        else:
            continue
        for runtime_target in selected_targets:
            if runtime_target == "js" and not node_available:
                status = "failed" if require_node else "skipped"
                results.append(
                    CaseResult(case.id, "js", status, "Node.js is not available")
                )
                continue
            try:
                actual = runners[runtime_target](case, source, timeout)
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                results.append(CaseResult(case.id, runtime_target, "failed", str(exc)))
                continue
            if actual == case.expected_stdout:
                results.append(CaseResult(case.id, runtime_target, "passed"))
            else:
                results.append(
                    CaseResult(
                        case.id,
                        runtime_target,
                        "failed",
                        f"expected stdout {case.expected_stdout!r}, got {actual!r}",
                    )
                )
    return results


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--all-retained",
        action="store_true",
        help="Run the spec-declared current and immediately preceding corpora",
    )
    parser.add_argument("--target", choices=("all", *ALL_TARGETS), default="all")
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--require-node", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.all_retained and args.manifest is not None:
            raise ManifestError("--manifest and --all-retained are mutually exclusive")
        manifest_paths = (
            retained_manifest_paths()
            if args.all_retained
            else (args.manifest or DEFAULT_MANIFEST,)
        )
        suites: list[tuple[ConformanceManifest, list[CaseResult]]] = []
        for manifest_path in manifest_paths:
            manifest = load_manifest(manifest_path)
            results = run_suite(
                manifest,
                target=args.target,
                case_ids=frozenset(args.case_ids) if args.case_ids else None,
                timeout=args.timeout,
                require_node=args.require_node,
            )
            suites.append((manifest, results))
    except (ManifestError, ValueError) as exc:
        print(f"conformance error: {exc}", file=sys.stderr)
        return 2

    if args.as_json:
        print(
            json.dumps(
                {
                    "corpora": [
                        {
                            "language_version": manifest.language_version,
                            "results": [asdict(result) for result in results],
                        }
                        for manifest, results in suites
                    ]
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        for manifest, results in suites:
            print(
                f"Geno {manifest.language_version} conformance: "
                f"{len(manifest.cases)} frozen cases"
            )
            for result in results:
                detail = f" - {result.detail}" if result.detail else ""
                print(
                    f"[{result.status.upper():7}] "
                    f"{result.case_id} ({result.target}){detail}"
                )

    all_results = [result for _manifest, results in suites for result in results]
    failed = sum(result.status == "failed" for result in all_results)
    skipped = sum(result.status == "skipped" for result in all_results)
    passed = sum(result.status == "passed" for result in all_results)
    if not args.as_json:
        print(f"Summary: {passed} passed, {failed} failed, {skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
