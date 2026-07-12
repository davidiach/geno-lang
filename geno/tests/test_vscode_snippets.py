"""Regression tests for VS Code Geno snippets."""

import json
from pathlib import Path

from geno.api import check

ROOT = Path(__file__).resolve().parents[2]


def test_async_function_snippet_typechecks_without_example_clause():
    snippets_path = ROOT / "vscode-geno" / "snippets" / "geno.json"
    snippets = json.loads(snippets_path.read_text(encoding="utf-8"))
    body = "\n".join(snippets["Async Function"]["body"])
    source = (
        body.replace("${1:name}", "fetch")
        .replace("${2:params}", "url: String")
        .replace("${3:ReturnType}", "String")
        .replace("${0}", "return url")
    )

    result = check(source)

    assert result.ok, [diagnostic.message for diagnostic in result.diagnostics]
