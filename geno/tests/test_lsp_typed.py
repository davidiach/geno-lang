"""Editor features that read the checked program (milestone M9).

Types of locals the checker inferred, method calls resolved to the function
they call, completion of a receiver's methods, and diagnostics' fixes as
code actions.
"""

from __future__ import annotations

import pytest

types = pytest.importorskip("lsprotocol.types")
workspace = pytest.importorskip("pygls.workspace")

from geno.lsp_server import create_server  # noqa: E402

SOURCE = """type Shape = Circle(radius: Float) | Square(side: Float)

trait Describe
    func describe(self: Self) -> String
end trait

impl Describe for Shape
    func describe(self: Shape) -> String
        example Circle(1.0) -> "circle"
        return match self with
            | Circle(_) -> "circle"
            | Square(_) -> "square"
        end match
    end func
end impl

func largest[T: Ord](xs: List[T], fallback: T) -> T
    example ([3, 1], 0) -> 3
    var best = fallback
    for x in xs do
        if x > best then
            best = x
        end if
    end for
    return best
end func

func main() -> Unit
    let xs = [3, 1, 2]
    let doubled = xs.map(fn(x) -> x * 2)
    let top = largest(doubled, 0)
    let pair = (top, "label")
    let first = pair.0
    let ages = {"ada": 36}
    let shape = Circle(2.0)
    print(shape.describe())
    print(ages.contains_key("ada"))
    print(first + 1)
end func
"""

URI = "untitled:Typed.geno"


class _Editor:
    def __init__(self, source: str, uri: str = URI) -> None:
        self.uri = uri
        self.server = create_server(diag_debounce_sec=0)
        self.server.lsp._workspace = workspace.Workspace(
            None, sync_kind=types.TextDocumentSyncKind.Full
        )
        self.published: dict[str, list] = {}
        self.server.publish_diagnostics = lambda uri, ds: self.published.__setitem__(
            uri, list(ds)
        )
        self.source = source
        self._handler(types.TEXT_DOCUMENT_DID_OPEN)(
            types.DidOpenTextDocumentParams(
                text_document=types.TextDocumentItem(
                    uri=self.uri, language_id="geno", version=1, text=source
                )
            )
        )

    def _handler(self, method: str):
        return self.server.lsp._get_handler(method)

    def edit(self, source: str) -> None:
        self.source = source
        self._handler(types.TEXT_DOCUMENT_DID_CHANGE)(
            types.DidChangeTextDocumentParams(
                text_document=types.VersionedTextDocumentIdentifier(
                    uri=self.uri, version=2
                ),
                content_changes=[
                    types.TextDocumentContentChangeEvent_Type2(text=source)
                ],
            )
        )

    def position(self, needle: str, offset: int = 0):
        for line, text in enumerate(self.source.split("\n")):
            column = text.find(needle)
            if column >= 0:
                return types.Position(line=line, character=column + offset)
        raise AssertionError(needle)

    @property
    def document(self):
        return types.TextDocumentIdentifier(uri=self.uri)

    def hover(self, needle: str, offset: int = 0) -> str | None:
        result = self._handler(types.TEXT_DOCUMENT_HOVER)(
            types.HoverParams(
                text_document=self.document, position=self.position(needle, offset)
            )
        )
        return None if result is None else result.contents.value

    def definition(self, needle: str, offset: int = 0):
        return self._handler(types.TEXT_DOCUMENT_DEFINITION)(
            types.DefinitionParams(
                text_document=self.document, position=self.position(needle, offset)
            )
        )

    def completion(self, needle: str, offset: int = 0) -> dict:
        result = self._handler(types.TEXT_DOCUMENT_COMPLETION)(
            types.CompletionParams(
                text_document=self.document, position=self.position(needle, offset)
            )
        )
        return {item.label: item for item in result.items}

    def signature(self, needle: str, offset: int = 0):
        return self._handler(types.TEXT_DOCUMENT_SIGNATURE_HELP)(
            types.SignatureHelpParams(
                text_document=self.document, position=self.position(needle, offset)
            )
        )


@pytest.fixture(scope="module")
def editor() -> _Editor:
    return _Editor(SOURCE)


@pytest.mark.parametrize(
    ("needle", "offset", "label", "kind"),
    [
        ("let doubled", 5, "doubled: List[Int]", "(variable)"),
        ("fn(x) -> x * 2", 3, "x: Int", "(variable)"),
        ("x * 2", 0, "x: Int", "(variable)"),
        ("for x in", 4, "x: T", "(variable)"),
        ("fallback: T", 0, "fallback: T", "(parameter)"),
        ("let first", 5, "first: Int", "(variable)"),
        ("pair.0", 5, "0: Int", "(field)"),
        ("let ages", 5, "ages: Map[String, Int]", "(variable)"),
        ("first + 1", 0, "first: Int", "(variable)"),
    ],
)
def test_hover_shows_the_checked_type(editor, needle, offset, label, kind) -> None:
    contents = editor.hover(needle, offset)
    assert contents is not None
    assert f"```\n{label}\n```" in contents
    assert kind in contents


def test_hover_on_a_method_names_the_function_it_calls(editor) -> None:
    contents = editor.hover("contains_key", 2)
    assert "contains_key(K) -> Bool" in contents
    assert "method, calls `map_contains_key`" in contents
    assert "map((T) -> U) -> List[Int]" in editor.hover(".map(", 2)


def test_hover_on_a_function_keeps_its_signature(editor) -> None:
    assert "largest[T: Ord](xs: List[T], fallback: T) -> T" in editor.hover(
        "largest(doubled", 2
    )


def test_definition_of_a_local_is_its_binding(editor) -> None:
    location = editor.definition("x * 2")
    binding = editor.position("fn(x)", 3)
    assert (location.range.start.line, location.range.start.character) == (
        binding.line,
        binding.character,
    )


def test_definition_and_hover_follow_block_scope() -> None:
    """A binding in a closed block does not shadow the outer one after it."""
    scoped = _Editor(
        "func main() -> Unit\n"
        "    let x = 1\n"
        "    if true then\n"
        '        let x = "inner"\n'
        "        print(x)\n"
        "    end if\n"
        "    print(x)\n"
        "end func\n"
    )
    for line, binding_line, label in [(6, 1, "x: Int"), (4, 3, "x: String")]:
        position = types.Position(
            line=line, character=scoped.source.split("\n")[line].index("(x") + 1
        )
        location = scoped._handler(types.TEXT_DOCUMENT_DEFINITION)(
            types.DefinitionParams(text_document=scoped.document, position=position)
        )
        assert location.range.start.line == binding_line
        hover = scoped._handler(types.TEXT_DOCUMENT_HOVER)(
            types.HoverParams(text_document=scoped.document, position=position)
        )
        assert f"```\n{label}\n```" in hover.contents.value
        assert scoped.source.split("\n")[binding_line].strip() in hover.contents.value


def test_definition_of_a_trait_method_is_the_receivers_impl(editor) -> None:
    location = editor.definition("shape.describe", 7)
    impl_method = editor.position("func describe(self: Shape)", 5)
    assert (location.range.start.line, location.range.start.character) == (
        impl_method.line,
        impl_method.character,
    )


def test_signature_help_inside_a_method_call_leaves_out_the_receiver(editor) -> None:
    help_ = editor.signature('contains_key("', 13)
    assert [s.label for s in help_.signatures] == ["contains_key(key: K) -> Bool"]


def test_completion_after_a_dot_offers_the_receivers_methods() -> None:
    editor = _Editor(SOURCE)
    editor.edit(
        SOURCE.replace("    print(first + 1)\n", "    print(first + 1)\n    xs.\n")
    )
    items = editor.completion("    xs.", 7)
    assert {"map", "filter", "length", "sort"} <= items.keys()
    assert "func" not in items  # not the keyword list
    assert items["map"].kind == types.CompletionItemKind.Method
    assert items["map"].detail.startswith("map((T) -> U) -> List[U]")
    assert "(list_map)" in items["map"].detail
    assert "abs" not in items  # numeric builtins take only Int and Float


def test_completion_lists_a_records_fields_and_trait_methods() -> None:
    source = SOURCE.replace(
        "type Shape = Circle(radius: Float) | Square(side: Float)",
        "type Shape = Circle(radius: Float)",
    ).replace('            | Square(_) -> "square"\n', "")
    editor = _Editor(source)
    editor.edit(
        source.replace("    print(first + 1)\n", "    print(first + 1)\n    shape.\n")
    )
    items = editor.completion("    shape.", 10)
    assert items["radius"].kind == types.CompletionItemKind.Field
    assert items["describe"].kind == types.CompletionItemKind.Method


def test_a_fix_becomes_a_quick_fix_over_the_misspelled_name() -> None:
    source = "func main() -> Unit\n    print([1].lenght())\nend func\n"
    editor = _Editor(source)
    diagnostic = editor.published[URI][0]
    assert (diagnostic.range.start.character, diagnostic.range.end.character) == (
        14,
        20,
    )
    actions = editor._handler(types.TEXT_DOCUMENT_CODE_ACTION)(
        types.CodeActionParams(
            text_document=editor.document,
            range=diagnostic.range,
            context=types.CodeActionContext(diagnostics=[diagnostic]),
        )
    )
    fix = next(action for action in actions if action.title.startswith("Change"))
    assert fix.is_preferred
    [edit] = fix.edit.changes[URI]
    assert edit.new_text == "length"
    assert (edit.range.start.character, edit.range.end.character) == (14, 20)


def test_a_local_may_be_renamed_to_or_from_a_builtins_name(tmp_path) -> None:
    """A local shadows a builtin only in its scope (proposals 0011, 0012)."""
    source = (
        "func main() -> Unit\n"
        "    let total = [1, 2].sum()\n"
        "    let count = total + 1\n"
        "    print(count)\n"
        "end func\n"
    )
    path = tmp_path / "Main.geno"
    path.write_text(source, encoding="utf-8")
    editor = _Editor(source, path.as_uri())
    rename = editor._handler(types.TEXT_DOCUMENT_RENAME)

    def rename_at(needle: str, new_name: str):
        return rename(
            types.RenameParams(
                text_document=editor.document,
                position=editor.position(needle),
                new_name=new_name,
            )
        )

    to_builtin = rename_at("total =", "sum")
    assert to_builtin is not None
    assert {edit.new_text for edit in to_builtin.changes[editor.uri]} == {"sum"}
    from_builtin = rename_at("count =", "tally")
    assert from_builtin is not None
    assert len(from_builtin.changes[editor.uri]) == 2


def test_a_top_level_function_keeps_off_builtin_names(tmp_path) -> None:
    """Renaming one to `count` would capture every call of the builtin."""
    source = (
        "func helper(n: Int) -> Int\n    example 1 -> 1\n    return n\nend func\n\n"
        "func main() -> Unit\n    print(helper(1))\nend func\n"
    )
    path = tmp_path / "Main.geno"
    path.write_text(source, encoding="utf-8")
    editor = _Editor(source, path.as_uri())
    result = editor._handler(types.TEXT_DOCUMENT_RENAME)(
        types.RenameParams(
            text_document=editor.document,
            position=editor.position("helper(n"),
            new_name="count",
        )
    )
    assert result is None
