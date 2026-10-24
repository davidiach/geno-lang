"""Strict TypeScript consumers of Geno's actual JavaScript Option values."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from geno.js_compiler import generate_dts
from geno.parser import parse

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = """
export func maybe_number(present: Bool) -> Option[Int]
    example true -> Some(7)
    example false -> None
    if present then
        return Some(7)
    end if
    return None
end func

export func read_number(value: Option[Int]) -> Int
    example Some(7) -> 7
    example None -> -1
    match value with
        | Some(number) -> return number
        | None -> return -1
    end match
end func

export func nested_option(present: Bool) -> Option[Option[Int]]
    example true -> Some(Some(7))
    example false -> Some(None)
    return Some(maybe_number(present))
end func

export func read_nested(value: Option[Option[Int]]) -> Int
    example Some(Some(7)) -> 7
    example Some(None) -> -1
    example None -> -2
    match value with
        | Some(inner) -> return read_number(inner)
        | None -> return -2
    end match
end func

export func option_list() -> List[Option[Int]]
    example () -> [Some(7), None]
    return [Some(7), None]
end func

export func echo_option[T](value: Option[T]) -> Option[T]
    example Some(7) -> Some(7)
    return value
end func

export func echo_unit(value: Option[Unit]) -> Option[Unit]
    example Some(()) -> Some(())
    example None -> None
    return value
end func

export func unit_values() -> Option[List[Option[Unit]]]
    example () -> Some([Some(()), None])
    return Some([Some(()), None])
end func

export func unit_list() -> Option[List[Unit]]
    example () -> Some([()])
    return Some([()])
end func

export func noop() -> Unit
    example () -> ()
    return ()
end func
"""


def test_option_declarations_describe_tags_and_payload_types():
    declarations = generate_dts(parse(LIBRARY))
    assert "readonly _tag: 'Some'" in declarations
    assert "readonly value: number" in declarations
    assert "readonly _tag: 'None'" in declarations
    assert "readonly value: T" in declarations
    assert "| null" not in declarations
    assert "readonly value: null" in declarations
    assert "export declare function noop(): void;" in declarations


@pytest.fixture
def typescript_library(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the TypeScript consumer test")
    tsc = Path(
        os.environ.get(
            "GENO_TEST_TSC", str(ROOT / "vscode-geno/node_modules/typescript/bin/tsc")
        )
    )
    if not tsc.is_file():
        pytest.skip(
            "TypeScript is not installed; run npm ci in vscode-geno or set "
            "GENO_TEST_TSC to its bin/tsc JavaScript entrypoint"
        )
    source = tmp_path / "Library.geno"
    source.write_text(LIBRARY, encoding="utf-8")
    (tmp_path / "package.json").write_text('{"type":"module"}', encoding="utf-8")
    compiled = subprocess.run(
        [
            sys.executable,
            "-m",
            "geno",
            "compile",
            str(source),
            "--target",
            "js",
            "--esm",
            "-o",
            str(tmp_path / "library.js"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert compiled.returncode == 0, compiled.stderr
    assert (tmp_path / "library.d.ts").is_file()
    return tmp_path, node, tsc


def _typecheck(directory, node, tsc, filename):
    return subprocess.run(
        [
            node,
            str(tsc),
            "--pretty",
            "false",
            "--strict",
            "--module",
            "nodenext",
            "--target",
            "es2020",
            "--noEmitOnError",
            filename,
        ],
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_tagged_option_consumers_typecheck_and_execute(typescript_library):
    directory, node, tsc = typescript_library
    (directory / "consumer.ts").write_text(
        """
import {
    maybe_number, read_number, nested_option, read_nested, option_list, echo_option,
    echo_unit, unit_values, unit_list
} from "./library.js";

for (const present of [true, false]) {
    const result = maybe_number(present);
    console.log(result._tag === "Some" ? result.value.toFixed(1) : "none");
    console.log(read_number(result));
    const nested = nested_option(present);
    console.log(read_nested(nested));
    if (nested._tag === "Some" && nested.value._tag === "Some") {
        console.log(nested.value.value.toFixed(1));
    }
}
console.log(read_number({ _tag: "Some", value: 12 }));
console.log(read_number({ _tag: "None" }));
console.log(read_nested({ _tag: "Some", value: { _tag: "None" } }));
console.log(read_nested({ _tag: "None" }));
console.log(option_list().map(read_number).join(","));
const word = echo_option({ _tag: "Some", value: "geno" });
if (word._tag === "Some") console.log(word.value.toUpperCase());
console.log(JSON.stringify(echo_unit({ _tag: "Some", value: null })));
console.log(JSON.stringify(echo_unit({ _tag: "None" })));
const units = unit_values();
if (units._tag === "Some") {
    for (const unit of units.value) {
        if (unit._tag === "Some") {
            const payload: null = unit.value;
            console.log(JSON.stringify(payload));
        } else console.log("none");
    }
}
const list = unit_list();
if (list._tag === "Some") {
    const values: null[] = list.value;
    console.log(JSON.stringify(values));
}
""",
        encoding="utf-8",
    )
    checked = _typecheck(directory, node, tsc, "consumer.ts")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    executed = subprocess.run(
        [node, "consumer.js"],
        cwd=directory,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert executed.returncode == 0, executed.stderr
    assert executed.stderr == ""
    assert executed.stdout == (
        "7.0\n7\n7\n7.0\nnone\n-1\n-1\n12\n-1\n-1\n-2\n7,-1\nGENO\n"
        '{"_tag":"Some","value":null}\n{"_tag":"None"}\nnull\nnone\n[null]\n'
    )


@pytest.mark.parametrize(
    "misuse",
    [
        "const scalar: number | null = maybe_number(true);",
        "const result = maybe_number(true); if (result !== null) result.toFixed(1);",
        "read_number(null);",
        "read_number(7);",
        'read_number({ _tag: "Some", value: "wrong" });',
        'read_nested({ _tag: "Some", value: 7 });',
        'echo_unit({ _tag: "Some", value: undefined });',
    ],
)
def test_option_declarations_reject_scalar_and_null_misuse(typescript_library, misuse):
    directory, node, tsc = typescript_library
    (directory / "invalid.ts").write_text(
        'import { maybe_number, read_number, read_nested, echo_unit } from "./library.js";\n'
        + misuse
        + "\n",
        encoding="utf-8",
    )
    checked = _typecheck(directory, node, tsc, "invalid.ts")
    assert checked.returncode != 0
    diagnostics = checked.stdout + checked.stderr
    assert "invalid.ts(2," in diagnostics
    assert "error TS" in diagnostics
    assert not (directory / "invalid.js").exists()
