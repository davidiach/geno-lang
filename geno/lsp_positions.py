"""Translate protocol positions at the LSP boundary, using source code points internally."""

from __future__ import annotations

import copy
from typing import Any, Callable


def convert_positions(
    value: Any,
    source_for_uri: Callable[[str], str | None],
    *,
    uri: str | None = None,
    encoding: str = "utf-16",
    to_client: bool,
) -> Any:
    """Copy an LSP payload, translating each position in its owning document.

    The server uses Python code-point columns throughout analysis. Only incoming
    requests and outgoing results/diagnostics cross this boundary. Workspace
    edits and locations carry their own URI; other ranges use the request URI.
    """
    from attrs import fields, has
    from lsprotocol import types

    lines_by_uri: dict[str, list[str]] = {}

    def convert(item: Any, document_uri: str | None) -> Any:
        if isinstance(item, types.Position):
            if document_uri is not None and document_uri not in lines_by_uri:
                source = source_for_uri(document_uri)
                lines_by_uri[document_uri] = (
                    source.split("\n") if source is not None else []
                )
            lines = lines_by_uri.get(document_uri or "", [])
            if not 0 <= item.line < len(lines) or item.character < 0:
                return copy.copy(item)
            line = lines[item.line]

            def units(text: str) -> int:
                if encoding == "utf-8":
                    return len(text.encode("utf-8"))
                if encoding == "utf-32":
                    return len(text)
                return len(text.encode("utf-16-le")) // 2

            if to_client:
                character = units(line[: item.character])
            else:
                character = 0
                offset = 0
                for char in line:
                    if offset >= item.character:
                        break
                    offset += units(char)
                    character += 1
            return types.Position(line=item.line, character=character)
        if isinstance(item, list):
            return [convert(child, document_uri) for child in item]
        if isinstance(item, tuple):
            return tuple(convert(child, document_uri) for child in item)
        if isinstance(item, dict):
            return {key: convert(child, document_uri) for key, child in item.items()}
        if not has(type(item)) or not type(item).__module__.startswith("lsprotocol"):
            return item

        document = getattr(item, "text_document", None)
        owner_uri = getattr(item, "uri", None) or getattr(document, "uri", None)
        document_uri = owner_uri or document_uri
        result = copy.copy(item)
        for field in fields(type(item)):
            child = getattr(item, field.name)
            if isinstance(item, types.WorkspaceEdit) and field.name == "changes":
                converted = (
                    {key: convert(edits, key) for key, edits in child.items()}
                    if child is not None
                    else None
                )
            else:
                converted = convert(child, document_uri)
            setattr(result, field.name, converted)
        return result

    return convert(value, uri)
