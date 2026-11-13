"""File-scoped interpreter tests must lower typed JSON in imported bodies."""

from pathlib import Path

import pytest

from geno.test_runner import run_test_suite


@pytest.mark.parametrize("with_manifest", [False, True])
def test_file_examples_can_call_typed_json_through_imports(
    tmp_path: Path, with_manifest: bool
) -> None:
    if with_manifest:
        (tmp_path / "geno.toml").write_text(
            'entrypoint = "Wrapper"\nfiles = ["Main", "Wrapper", "Codec"]\n',
            encoding="utf-8",
        )
    (tmp_path / "Codec.geno").write_text(
        """
export func encode_number(value: Int) -> String
    example 3 -> "3"
    return json_encode(value)
end func
""",
        encoding="utf-8",
    )
    (tmp_path / "Wrapper.geno").write_text(
        """
import Codec as Json

export func render(value: Int) -> String
    example 3 -> "3"
    return Json.encode_number(value)
end func
""",
        encoding="utf-8",
    )
    entry = tmp_path / "Main.geno"
    entry.write_text(
        """
import Wrapper

func probe(value: Int) -> String
    example 3 -> "3"
    return Wrapper.render(value)
end func
""",
        encoding="utf-8",
    )

    suite = run_test_suite([entry])

    assert suite.success, suite.to_dict()
    assert suite.total == 1
