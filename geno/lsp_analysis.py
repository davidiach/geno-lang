"""What the type checker knows about a document, indexed for the editor.

The language server asks this module for the type of a name under the cursor
(locals, parameters, inferred lambda and loop variables, pattern bindings),
for the function a method call resolves to, and for the methods a value's
type offers after ``.``. It checks the document the way ``geno check`` does,
keeping the annotated syntax tree, and records every binding the checker
makes (``TypeChecker.binding_types``).
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .ast_nodes import (
    FieldAccess,
    FunctionCall,
    FunctionDef,
    Identifier,
    ImplDef,
    IndexAccess,
    IntegerLiteral,
    Program,
)
from .tokens import SourceLocation
from .types import FloatType, FuncType, IntType, Type, TypeEnv, TypeVar, UserType
from .types import TypeError as GenoTypeError

if TYPE_CHECKING:
    from .typechecker import TypeChecker

# Checking stops at these; what it checked first keeps its types.
_CHECK_ERRORS = (GenoTypeError, ValueError, KeyError, RecursionError)

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+")


@dataclass(frozen=True)
class Fact:
    """One name the checker typed: where it is, what it is, its type."""

    line: int  # 1-based
    column: int  # 1-based column of the name itself
    name: str
    type: Type
    kind: str  # "local", "parameter", "use", "method" or "field"
    target: str | None = None  # the function a method call resolved to
    result: Type | None = None  # what a method call returns
    receiver: Type | None = None  # the type a method was called on


@dataclass
class Analysis:
    """A checked document and the facts recorded while checking it."""

    source: str
    program: Program
    checker: TypeChecker
    facts: dict[int, list[Fact]]
    scopes: list[tuple[int, int]]  # line ranges of functions and methods
    # Each local's use, (line, column), and where the binding it resolves to
    # is written, as the checker's lexical scopes found it.
    uses: dict[tuple[int, int], tuple[int, int]] = field(default_factory=dict)

    def fact_at(self, line: int, character: int) -> Fact | None:
        """The fact for the name at 0-based ``line``/``character``."""
        lines = self.source.splitlines()
        if not 0 <= line < len(lines):
            return None
        text = lines[line]
        for match in _WORD.finditer(text):
            if match.start() <= character <= match.end():
                word, column = match.group(), match.start() + 1
                break
        else:
            return None
        candidates = [
            fact for fact in self.facts.get(line + 1, ()) if fact.name == word
        ]
        exact = [fact for fact in candidates if fact.column == column]
        if exact:
            # A binding is reported in preference to a use at the same place.
            exact.sort(key=lambda fact: fact.kind == "use")
            return exact[0]
        return None

    def type_of(self, name: str, line: int) -> Type | None:
        """The type of local ``name`` as last bound or used before 0-based ``line``.

        Used for completion, when the document under the cursor may not parse.
        """
        scope = self._scope_of(line + 1)
        best: Fact | None = None
        for fact_line, facts in self.facts.items():
            if fact_line > line + 1 or (scope and not _within(scope, fact_line)):
                continue
            for fact in facts:
                if fact.name != name or fact.kind in ("method", "field"):
                    continue
                if best is None or (fact.line, fact.column) > (best.line, best.column):
                    best = fact
        return best.type if best is not None else None

    def binding_of(self, fact: Fact) -> Fact | None:
        """The binding a local's use refers to, by the checker's scopes.

        A binding inside a block that has closed is not the one a later use
        of the same name refers to.
        """
        if fact.kind != "use":
            return None
        written = self.uses.get((fact.line, fact.column))
        if written is None:
            return None
        line, column = written
        lines = self.source.splitlines()
        name_column = _name_column(lines, line, column, fact.name)
        for other in self.facts.get(line, ()):
            if (
                other.name == fact.name
                and other.kind in ("local", "parameter")
                and other.column == name_column
            ):
                return other
        return None

    def is_local(self, fact: Fact) -> bool:
        """Whether a use names a binding of its own function, not a global."""
        if fact.kind != "use":
            return True
        return (fact.line, fact.column) in self.uses

    def methods_for(self, receiver: Type) -> list[tuple[str, str, str | None]]:
        """``(method, signature, target)`` for each method ``receiver`` offers.

        The signature leaves out the receiver, as a method call does.
        """
        checker = self.checker
        env = checker.global_env
        entries: list[tuple[str, str, str | None]] = []
        if isinstance(receiver, UserType):
            info = checker.type_defs.get(receiver.name)
            if info is not None and len(info.variants) == 1:
                for field_name, field_type in next(iter(info.variants.values())):
                    entries.append((field_name, str(field_type), None))
        numeric = isinstance(receiver, (IntType, FloatType))
        for method in checker._method_names_for(receiver, env):
            target = checker._method_target(method, receiver, env)
            function_type = env.lookup(target) if target else None
            if not isinstance(function_type, FuncType):
                continue
            first = function_type.param_types[0]
            if isinstance(first, TypeVar) and first.name == "Num" and not numeric:
                continue  # numeric builtins take only Int and Float
            entries.append((method, method_signature(method, function_type), target))
        return entries

    def method_call_signature(
        self, receiver: Type, method: str
    ) -> tuple[str, list[str]] | None:
        """Label and parameters of ``receiver.method(...)``, receiver left out."""
        checker = self.checker
        env = checker.global_env
        target = checker._method_target(method, receiver, env)
        function_type = env.lookup(target) if target else None
        if target is None or not isinstance(function_type, FuncType):
            return None
        names = checker.func_param_names.get(target)
        if names is None:
            builtin = checker.builtin_types.get(target)
            names = list(builtin[1]) if builtin is not None else []
        params = [
            f"{names[index]}: {_display(param)}"
            if index < len(names)
            else _display(param)
            for index, param in enumerate(function_type.param_types)
        ][1:]
        result = _display(function_type.return_type)
        return f"{method}({', '.join(params)}) -> {result}", params

    def _scope_of(self, line: int) -> tuple[int, int] | None:
        for scope in self.scopes:
            if _within(scope, line):
                return scope
        return None


def method_signature(method: str, function_type: Type | None) -> str:
    """``method(params) -> result`` without the receiver parameter."""
    if not isinstance(function_type, FuncType):
        return method
    params = ", ".join(_display(param) for param in function_type.param_types[1:])
    return f"{method}({params}) -> {_display(function_type.return_type)}"


def hover_label(fact: Fact) -> str:
    """The code line a hover shows for ``fact``."""
    if fact.kind != "method":
        return f"{fact.name}: {_display(fact.type)}"
    label = method_signature(fact.name, fact.type)
    if fact.result is not None and isinstance(fact.type, FuncType):
        label = label.rsplit(" -> ", 1)[0] + f" -> {_display(fact.result)}"
    return label


def display_type(type_: Type) -> str:
    """A type as diagnostics show it."""
    return _display(type_)


def _display(type_: Type) -> str:
    from .typechecker import _display_type

    return _display_type(type_)


def _within(scope: tuple[int, int], line: int) -> bool:
    return scope[0] <= line <= scope[1]


def analyze(source: str, path: Path | None) -> Analysis | None:
    """Check ``source`` (the document at ``path``, if on disk) and index it.

    Returns ``None`` when the document does not parse. Type errors do not
    stop the analysis: everything checked before an error keeps its type.
    """
    from .lexer import LexerError
    from .parser import ParseError, ParseErrors, parse
    from .typechecker import TypeChecker

    checker = TypeChecker()
    checker.binding_types = []
    program: Program | None = None
    if path is not None:
        checked = _check_in_project(checker, source, path)
        if checked is not None:
            program, checker = checked
    if program is None:
        try:
            program = parse(source, filename=str(path) if path else "<input>")
        except (LexerError, ParseError, ParseErrors, RecursionError):
            return None
        checker = TypeChecker()
        checker.binding_types = []
        with contextlib.suppress(*_CHECK_ERRORS):
            checker.check_program(program)
    filename = program.location.filename if program.location else None
    return Analysis(
        source=source,
        program=program,
        checker=checker,
        facts=_collect_facts(
            source, program, checker.binding_types, filename, checker.global_env
        ),
        scopes=_scopes(program, source),
        uses={
            (use.line, use.column): (binding.line, binding.column)
            for use, binding in checker.binding_uses
            if filename is None or use.filename == filename
        },
    )


def _check_in_project(
    checker: TypeChecker, source: str, path: Path
) -> tuple[Program, TypeChecker] | None:
    """Check ``path`` with its imports resolved, as ``geno check`` does.

    Returns the module's syntax tree and the checker that checked it.
    """
    from .dependency_graph import DependencyGraph, DependencyGraphError
    from .lexer import LexerError
    from .parser_base import ParseError, ParseErrors
    from .project_graph import ProjectGraph, ProjectGraphError
    from .project_resolution import ProjectResolutionError

    try:
        project = ProjectGraph.discover(path)
        graph = DependencyGraph.resolve(
            project, source_overrides={path.resolve(): source}
        )
    except (
        DependencyGraphError,
        ProjectGraphError,
        ProjectResolutionError,
        LexerError,
        ParseError,
        ParseErrors,
        OSError,
        ValueError,
    ):
        return None  # check the document on its own instead
    module = next(
        (
            name
            for name, resolved in graph.file_map.items()
            if resolved.path.resolve() == path.resolve()
        ),
        None,
    )
    if module is None:
        return None
    with contextlib.suppress(*_CHECK_ERRORS):
        checker.check_project_graph(graph)
    program = graph.parsed.get(module)
    module_checker = checker.module_checkers.get(module)
    if program is None or module_checker is None:
        return None  # an imported module failed before this one was checked
    return program, module_checker


def _scopes(program: Program, source: str) -> list[tuple[int, int]]:
    """Line ranges of each function, impl method and test, outermost last."""
    starts: list[int] = []
    for defn in program.definitions:
        if isinstance(defn, ImplDef):
            starts.extend(method.location.line for method in defn.methods)
        elif defn.location is not None:
            starts.append(defn.location.line)
    starts = sorted(set(starts))
    total = len(source.splitlines())
    return [
        (start, (starts[index + 1] - 1) if index + 1 < len(starts) else total)
        for index, start in enumerate(starts)
    ]


def _collect_facts(
    source: str,
    program: Program,
    bindings: list[tuple[SourceLocation, str, Type]] | None,
    filename: str | None,
    env: TypeEnv,
) -> dict[int, list[Fact]]:
    lines = source.splitlines()
    facts: dict[int, list[Fact]] = {}
    seen: set[tuple[int, int, str, str]] = set()

    def add(fact: Fact) -> None:
        key = (fact.line, fact.column, fact.name, fact.kind)
        if key not in seen:
            seen.add(key)
            facts.setdefault(fact.line, []).append(fact)

    parameters = {
        (param.location.line, param.location.column)
        for node in _walk(program)
        if isinstance(node, FunctionDef)
        for param in node.params
    }
    for location, name, type_ in bindings or ():
        if filename is not None and location.filename != filename:
            continue
        column = _name_column(lines, location.line, location.column, name)
        if column is None:
            continue
        kind = (
            "parameter" if (location.line, location.column) in parameters else "local"
        )
        add(Fact(location.line, column, name, type_, kind))

    rewritten: set[int] = set()
    for node in _walk(program):
        if isinstance(node, FunctionCall) and node._method_call is not None:
            rewritten.add(id(node.function))
            _add_method_fact(node, lines, add, env)
    for node in _walk(program):
        if isinstance(node, Identifier) and id(node) not in rewritten:
            use_type = node._resolved_type
            if use_type is not None and node.location is not None:
                add(
                    Fact(
                        node.location.line,
                        node.location.column,
                        node.name,
                        use_type,
                        "use",
                    )
                )
        elif isinstance(node, FieldAccess) and node._resolved_type is not None:
            position = _member_position(lines, node.location, node.field_name, 0)
            if position is not None:
                add(Fact(*position, node.field_name, node._resolved_type, "field"))
        elif (
            isinstance(node, IndexAccess)
            and isinstance(node.index, IntegerLiteral)
            and node._resolved_type is not None
            and node.index.location is not None
            and _follows_dot(lines, node.index.location)
        ):
            # A tuple field, `pair.0`.
            index = node.index.location
            add(
                Fact(
                    index.line,
                    index.column,
                    str(node.index.value),
                    node._resolved_type,
                    "field",
                )
            )
    return facts


def _follows_dot(lines: list[str], location: SourceLocation) -> bool:
    text = lines[location.line - 1] if 0 < location.line <= len(lines) else ""
    return text[: location.column - 1].rstrip().endswith(".")


def _add_method_fact(
    call: FunctionCall,
    lines: list[str],
    add: Callable[[Fact], None],
    env: TypeEnv,
) -> None:
    method = call._method_call
    assert method is not None
    target = call.function.name if isinstance(call.function, Identifier) else None
    # `xs.map(f).map(g)`: both calls start at `xs`; the outer is the second
    # `.map` after it.
    occurrence = 0
    receiver = call.arguments[0].value if call.arguments else None
    while isinstance(receiver, FunctionCall) and receiver._method_call is not None:
        if receiver._method_call == method:
            occurrence += 1
        receiver = receiver.arguments[0].value if receiver.arguments else None
    position = _member_position(lines, call.function.location, method, occurrence)
    function_type = call.function._resolved_type
    if function_type is None and target is not None:
        function_type = env.lookup(target)
    if position is not None and function_type is not None:
        receiver_type = (
            call.arguments[0].value._resolved_type if call.arguments else None
        )
        add(
            Fact(
                *position,
                method,
                function_type,
                "method",
                target,
                call._resolved_type,
                receiver_type,
            )
        )


def _member_position(
    lines: list[str], start: SourceLocation | None, name: str, occurrence: int
) -> tuple[int, int] | None:
    """Where ``.name`` appears for the ``occurrence``-th time after ``start``."""
    if start is None:
        return None
    pattern = re.compile(r"\.\s*(" + re.escape(name) + r")\b")
    seen = 0
    for index in range(start.line - 1, len(lines)):
        offset = start.column - 1 if index == start.line - 1 else 0
        for match in pattern.finditer(lines[index], offset):
            if seen == occurrence:
                return index + 1, match.start(1) + 1
            seen += 1
    return None


def _name_column(lines: list[str], line: int, column: int, name: str) -> int | None:
    """Column of ``name`` in the binding construct starting at ``line``/``column``."""
    if not 0 < line <= len(lines):
        return None
    text = lines[line - 1]
    for match in re.finditer(r"\b" + re.escape(name) + r"\b", text):
        if match.start() + 1 >= column:
            return match.start() + 1
    return None


def _walk(node: object) -> Iterator[object]:
    """Every syntax node under ``node``."""
    stack = [node]
    seen: set[int] = set()
    while stack:
        current = stack.pop()
        if isinstance(current, (list, tuple)):
            stack.extend(current)
            continue
        if not is_dataclass(current) or isinstance(current, type):
            continue
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        for item in fields(current):
            if item.name.startswith("_") or item.name == "location":
                continue
            stack.append(getattr(current, item.name))
