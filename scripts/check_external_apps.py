#!/usr/bin/env python3
"""Qualify a candidate wheel against the pinned external Geno application corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import zipfile
from email.parser import BytesParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CORPUS_REPOSITORY = "oskarasi/geno-corpus"
CORPUS_REVISION = "977644bbc4a7ca3b0210cf8f024d9c9c67be2b42"
EXPECTED_APPS = 101
# IDs name evidence logs, so they stay plain file-name characters.
_APP_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

# Isolated Python ignores the caller's import path. The ordinary console script
# and corpus subprocesses also receive an environment without Python overrides.
IDENTITY_CODE = """
import importlib.metadata, json, platform, sys
import geno
package = importlib.metadata.distribution('geno-lang')
print(json.dumps({
    'version': package.version,
    'internal_version': geno.__version__,
    'package_path': geno.__file__,
    'distribution_path': str(package.locate_file('')),
    'prefix': sys.prefix,
    'python': sys.version,
    'platform': platform.platform(),
    'direct_url': json.loads(package.read_text('direct_url.json') or '{}'),
}))
"""


def wheel_identity(wheel: Path) -> dict[str, str]:
    """Read the distribution identity without importing source from the wheel."""
    if wheel.suffix != ".whl":
        raise ValueError("Candidate must be a wheel")
    with zipfile.ZipFile(wheel) as archive:
        metadata = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
        if len(metadata) != 1:
            raise ValueError("Expected exactly one wheel METADATA file")
        parsed = BytesParser().parsebytes(archive.read(metadata[0]))
    if parsed["Name"] != "geno-lang" or not parsed["Version"]:
        raise ValueError("Candidate must be a versioned geno-lang wheel")
    return {
        "filename": wheel.name,
        "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "version": str(parsed["Version"]),
    }


def validate_install(
    identity: dict[str, Any], candidate: dict[str, str], environment: Path
) -> None:
    """Reject another version, a source import, or a different local artifact."""
    if identity["version"] != candidate["version"]:
        raise ValueError("Installed distribution version does not match the wheel")
    if Path(identity["prefix"]).resolve() != environment.resolve():
        raise ValueError("Candidate interpreter is not in the fresh environment")
    for key in ("package_path", "distribution_path"):
        if not Path(identity[key]).resolve().is_relative_to(environment.resolve()):
            raise ValueError(f"Installed {key} is outside the fresh environment")
    hashes = identity["direct_url"].get("archive_info", {}).get("hashes", {})
    if hashes.get("sha256") != candidate["sha256"]:
        raise ValueError("Installed artifact hash does not match the candidate wheel")


def validate_manifest(manifest: dict[str, Any], corpus: Path) -> dict[str, str]:
    """Require the entire fixed corpus, with unique IDs and contained app paths."""
    apps = manifest["apps"]
    if len(apps) != EXPECTED_APPS:
        raise ValueError(f"Expected {EXPECTED_APPS} corpus applications")
    selected: dict[str, str] = {}
    for app in apps:
        app_id, tier = app["id"], app["tier"]
        if (
            not isinstance(app_id, str)
            or not _APP_ID.fullmatch(app_id)
            or app_id in selected
        ):
            raise ValueError("Corpus application IDs must be plain names and unique")
        if tier not in {"flagship", "reference", "corpus"}:
            raise ValueError(f"Unknown corpus tier: {tier}")
        if not (corpus / app["path"]).resolve().is_relative_to(corpus.resolve()):
            raise ValueError("Corpus application path escapes the checkout")
        selected[app_id] = tier
    return selected


_STAGES = ("ok", "test_ok", "run_ok", "compile_ok")


def failed_applications(
    results: dict[str, Any], selected: dict[str, str], executable: Path, version: str
) -> list[str]:
    """Check that *results* cover the corpus once; return the apps that failed.

    An application passes only when every stage reports exactly ``True``.
    """
    if results["tier"] != "all" or results["geno"] != str(executable):
        raise ValueError("Corpus did not use all tiers and the explicit candidate")
    if results["geno_version"] != version:
        raise ValueError("Corpus executable version does not match the candidate")
    apps = results["apps"]
    ids = [app["id"] for app in apps]
    if len(ids) != len(selected) or set(ids) != set(selected):
        raise ValueError("Corpus results do not cover every selected application once")
    failed = []
    for app in apps:
        if app["tier"] != selected[app["id"]]:
            raise ValueError(f"Application changed tier: {app['id']}")
        if any(app.get(key) is not True for key in _STAGES):
            failed.append(app["id"])
    if (results["total"], results["passed"], results["failed"]) != (
        len(selected),
        len(selected) - len(failed),
        len(failed),
    ):
        raise ValueError("Corpus summary counters are inconsistent")
    return failed


def validate_results(
    results: dict[str, Any], selected: dict[str, str], executable: Path, version: str
) -> None:
    """A green harness exit alone cannot prove full candidate coverage."""
    failed = failed_applications(results, selected, executable, version)
    if failed:
        raise ValueError(f"Application did not pass every stage: {failed[0]}")


class Commands:
    """Keep exact commands, outcomes, and logs even when qualification fails."""

    def __init__(self, output: Path) -> None:
        self.output = output
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
        self.env["PYTHONNOUSERSITE"] = "1"
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"
        self.sequence = 0
        self.returncode: int | None = None

    def run(
        self,
        name: str,
        args: list[str],
        cwd: Path,
        timeout: int = 180,
        *,
        check: bool = True,
    ) -> str:
        self.sequence += 1
        self.returncode = None
        log = self.output / "logs" / f"{self.sequence:02d}-{name}.log"
        log.parent.mkdir(exist_ok=True)
        started = time.monotonic()
        record: dict[str, Any] = {"command": args, "cwd": str(cwd), "log": str(log)}
        print(f"[{name}]", flush=True)
        try:
            with log.open("w", encoding="utf-8") as stream:
                result = subprocess.run(  # noqa: S603
                    args,
                    cwd=cwd,
                    env=self.env,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                    check=False,
                )
            record["returncode"] = self.returncode = result.returncode
            if result.returncode and check:
                raise RuntimeError(f"{name} failed ({result.returncode}); see {log}")
            return log.read_text(encoding="utf-8").strip()
        except subprocess.TimeoutExpired:
            record["timeout_seconds"] = timeout
            raise
        finally:
            record["seconds"] = round(time.monotonic() - started, 3)
            with (self.output / "commands.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record) + "\n")


def validate_corpus_checkout(commands: Commands, corpus: Path) -> None:
    revision = commands.run("corpus-revision", ["git", "rev-parse", "HEAD"], corpus)
    if revision != CORPUS_REVISION:
        raise ValueError("Corpus checkout does not match the pinned revision")
    status = commands.run(
        "corpus-status",
        ["git", "status", "--porcelain", "--untracked-files=all"],
        corpus,
    )
    if status:
        raise ValueError("Corpus checkout must be clean before qualification")


def qualify(wheel: Path, corpus: Path, output: Path, receipt: dict[str, Any]) -> None:
    commands = Commands(output)
    candidate = wheel_identity(wheel)
    receipt["candidate"] = candidate
    receipt["source_revision"] = commands.run(
        "source-revision", ["git", "rev-parse", "HEAD"], ROOT
    )
    receipt["source_status"] = commands.run(
        "source-status", ["git", "status", "--porcelain"], ROOT
    )
    validate_corpus_checkout(commands, corpus)
    selected = validate_manifest(
        json.loads((corpus / "manifest.json").read_text()), corpus
    )
    receipt["corpus"] = {
        "repository": CORPUS_REPOSITORY,
        "revision": CORPUS_REVISION,
        "applications": len(selected),
        "upstream_pin": json.loads((corpus / "pin.json").read_text()),
    }
    environment = output / "venv"
    commands.run(
        "create-environment", [sys.executable, "-m", "venv", str(environment)], output
    )
    bin_dir = environment / ("Scripts" if os.name == "nt" else "bin")
    python = bin_dir / ("python.exe" if os.name == "nt" else "python")
    executable = bin_dir / ("geno.exe" if os.name == "nt" else "geno")
    commands.run(
        "install-runtime",
        [
            str(python),
            "-I",
            "-m",
            "pip",
            "install",
            "--require-hashes",
            "-r",
            str(ROOT / "requirements.lock"),
        ],
        output,
    )
    commands.run(
        "install-candidate",
        [str(python), "-I", "-m", "pip", "install", "--no-deps", str(wheel)],
        output,
    )
    commands.run("pip-check", [str(python), "-I", "-m", "pip", "check"], output)
    identity = json.loads(
        commands.run("identity", [str(python), "-I", "-c", IDENTITY_CODE], output)
    )
    receipt["installed"] = identity
    validate_install(identity, candidate, environment)
    version = commands.run("candidate-version", [str(executable), "--version"], output)
    if version != f"geno {identity['internal_version']}":
        raise ValueError("Console script does not match the imported candidate")
    results = run_corpus(
        commands, "corpus", python, executable, corpus, output / "corpus.json"
    )
    failed = failed_applications(results, selected, executable, version)
    receipt["results"] = {k: results[k] for k in ("total", "passed", "failed")}
    if not failed:
        return

    # A release may break source compatibility when `geno migrate` carries
    # applications across it (proposal 0003): an application that fails as
    # written must pass after the candidate's own migration, and the receipt
    # names each one.
    migrated = output / "migrated-corpus"
    shutil.copytree(corpus, migrated, ignore=shutil.ignore_patterns(".git"))
    paths = {
        app["id"]: app["path"]
        for app in json.loads((corpus / "manifest.json").read_text())["apps"]
    }
    for app_id in failed:
        app_dir = migrated / paths[app_id]
        commands.run(
            f"migrate-diff-{app_id}",
            [str(executable), "migrate", "--diff", "."],
            app_dir,
        )
        commands.run(f"migrate-{app_id}", [str(executable), "migrate", "."], app_dir)
    migrated_results = run_corpus(
        commands,
        "migrated-corpus",
        python,
        executable,
        migrated,
        output / "corpus-migrated.json",
    )
    validate_results(migrated_results, selected, executable, version)
    receipt["migrated"] = {
        "applications": failed,
        "results": {k: migrated_results[k] for k in ("total", "passed", "failed")},
    }


def run_corpus(
    commands: Commands,
    name: str,
    python: Path,
    executable: Path,
    corpus: Path,
    json_out: Path,
) -> dict[str, Any]:
    """Run every tier of *corpus*; its exit must agree with its results."""
    commands.run(
        name,
        [
            str(python),
            "-I",
            str(corpus / "harness" / "run_corpus.py"),
            "--tier",
            "all",
            "--compile",
            "--geno-bin",
            str(executable),
            "--json-out",
            str(json_out),
        ],
        corpus,
        timeout=900,
        check=False,
    )
    returncode = commands.returncode
    if returncode and not json_out.exists():
        raise RuntimeError(f"{name} failed ({returncode}) without results")
    results: dict[str, Any] = json.loads(json_out.read_text())
    if bool(returncode) != bool(results.get("failed")):
        raise ValueError(f"{name} exit status disagrees with its results")
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument(
        "--output", required=True, type=Path, help="A new evidence directory"
    )
    args = parser.parse_args(argv)
    output = args.output.resolve()
    # Refuse stale environments/results. Never clean an existing user directory.
    output.mkdir(parents=True, exist_ok=False)
    receipt: dict[str, Any] = {
        "passed": False,
        "python": sys.version,
        "platform": platform.platform(),
    }
    try:
        qualify(args.wheel.resolve(), args.corpus.resolve(), output, receipt)
        receipt["passed"] = True
        migrated = receipt.get("migrated", {}).get("applications", [])
        after = f" ({len(migrated)} after geno migrate: {', '.join(migrated)})"
        print(
            f"PASS: all {EXPECTED_APPS} external applications passed "
            f"test/run/Python compilation{after if migrated else ''}"
        )
        return 0
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        RuntimeError,
        subprocess.SubprocessError,
        zipfile.BadZipFile,
    ) as exc:
        receipt["error"] = str(exc)
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    finally:
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
