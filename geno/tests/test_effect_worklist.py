"""Differential and complexity tests for adaptive effect stabilization."""

from __future__ import annotations

import random
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from geno import effect_stabilizer as effect_stabilizer_module
from geno.ast_nodes import FunctionDef, ImplDef, Program
from geno.parser import parse
from geno.typechecker import TypeChecker, TypeEnv
from geno.types import FuncType, GenoTypeError

_Effects = tuple[str, ...]
_Signature = tuple[str, _Effects]
_ImplSignature = tuple[str, str, tuple[_Signature, ...]]
_Diagnostic = tuple[object, str, str, int, int]
_AstMetadata = tuple[tuple[str, str, str], ...]
_Snapshot = tuple[
    bool,
    tuple[_Signature, ...],
    tuple[_ImplSignature, ...],
    tuple[_Diagnostic, ...],
    _AstMetadata,
]


def _stabilize_legacy(
    checker: TypeChecker,
    functions: list[FunctionDef],
    impl_methods: list[tuple[ImplDef, FunctionDef]],
) -> None:
    """Run the original fixed-point loop independently of production helpers."""
    while True:
        changed = False

        for defn in functions:
            existing_type = checker.global_env.lookup(defn.name)
            if not isinstance(existing_type, FuncType):
                continue
            final_effects = checker._stable_effects_for_function(defn)
            if existing_type.effects != final_effects:
                checker.global_env.bind(
                    defn.name,
                    FuncType(
                        existing_type.param_types,
                        existing_type.return_type,
                        final_effects,
                    ),
                )
                changed = True

        for impl_def, method in impl_methods:
            method_types = checker.impl_registry.get(
                (impl_def.trait_name, impl_def.target_type)
            )
            if method_types is None or method.name not in method_types:
                continue
            existing_type = method_types[method.name]
            final_effects = checker._stable_effects_for_function(method)
            if existing_type.effects != final_effects:
                method_types[method.name] = FuncType(
                    existing_type.param_types,
                    existing_type.return_type,
                    final_effects,
                )
                changed = True

        if not changed:
            return


class _LegacyChecker(TypeChecker):
    """Run the original fixed-point loop from its original frame."""

    def _stabilize_function_effects(self, program: Program) -> None:
        functions = [
            defn for defn in program.definitions if isinstance(defn, FunctionDef)
        ]
        impl_methods = [
            (defn, method)
            for defn in program.definitions
            if isinstance(defn, ImplDef)
            for method in defn.methods
        ]
        _stabilize_legacy(self, functions, impl_methods)


class _CountingChecker(TypeChecker):
    """Count stabilization inference without adding production instrumentation."""

    def __init__(self) -> None:
        super().__init__()
        self.stabilization_inferences = 0
        self._inside_stabilization = False

    def _stabilize_function_effects(self, program: Program) -> None:
        self._inside_stabilization = True
        try:
            super()._stabilize_function_effects(program)
        finally:
            self._inside_stabilization = False

    def _infer_function_effects(
        self, defn: FunctionDef, env: TypeEnv
    ) -> frozenset[str]:
        if self._inside_stabilization:
            self.stabilization_inferences += 1
        return super()._infer_function_effects(defn, env)


def _inject_delayed_diagnostic(
    checker: TypeChecker,
    defn: FunctionDef,
    _effects: frozenset[str],
) -> None:
    """Model an effect-dependent inference diagnostic with AST metadata."""
    provider = checker.global_env.lookup("f0")
    if (
        defn.name != "invalid"
        or not isinstance(provider, FuncType)
        or "io" not in provider.effects
    ):
        return
    message = "synthetic delayed effect diagnostic"
    if any(error.message == message for error in checker.errors):
        return
    failed_node = defn.body[0]
    failed_node.__dict__["_expected_runtime_type"] = checker.global_env.lookup("f0")
    checker._error(message, failed_node.location)


class _DelayedDiagnosticChecker(_CountingChecker):
    def _stable_effects_for_function(self, defn: FunctionDef) -> frozenset[str]:
        effects = super()._stable_effects_for_function(defn)
        _inject_delayed_diagnostic(self, defn, effects)
        return effects


class _DelayedDiagnosticLegacyChecker(_LegacyChecker):
    def _stable_effects_for_function(self, defn: FunctionDef) -> frozenset[str]:
        effects = super()._stable_effects_for_function(defn)
        _inject_delayed_diagnostic(self, defn, effects)
        return effects


def _ast_effect_metadata(program: Program) -> tuple[tuple[str, str, str], ...]:
    metadata: list[tuple[str, str, str]] = []

    def visit(value: object, path: str) -> None:
        if isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]")
            return
        if not hasattr(value, "__dict__"):
            return
        if value.__class__.__module__ != "geno.ast_nodes":
            return

        resolved = getattr(value, "_resolved_type", None)
        expected = getattr(value, "_expected_runtime_type", None)
        if resolved is not None or expected is not None:
            metadata.append((path, repr(resolved), repr(expected)))

        for name, child in vars(value).items():
            if not name.startswith("_"):
                visit(child, f"{path}.{name}")

    visit(program, "program")
    return tuple(metadata)


def _snapshot(
    source: str,
    *,
    checker: TypeChecker | None = None,
    modules: dict[str, Program] | None = None,
) -> _Snapshot:
    program = parse(source)
    checker = checker or TypeChecker()
    try:
        checker.check_program(program, modules=modules)
        succeeded = True
    except GenoTypeError:
        succeeded = False

    signatures: list[_Signature] = []
    for defn in program.definitions:
        if not isinstance(defn, FunctionDef):
            continue
        function_type = checker.global_env.lookup(defn.name)
        if isinstance(function_type, FuncType):
            signatures.append((defn.name, tuple(sorted(function_type.effects))))

    impl_signatures: list[_ImplSignature] = []
    for defn in program.definitions:
        if not isinstance(defn, ImplDef):
            continue
        method_types = checker.impl_registry.get(
            (defn.trait_name, defn.target_type), {}
        )
        impl_signatures.append(
            (
                defn.trait_name,
                defn.target_type,
                tuple(
                    (name, tuple(sorted(function_type.effects)))
                    for name, function_type in sorted(method_types.items())
                ),
            )
        )

    diagnostics = tuple(
        (
            error.error_code,
            error.message,
            error.location.filename,
            error.location.line,
            error.location.column,
        )
        for error in checker.errors
    )
    return (
        succeeded,
        tuple(signatures),
        tuple(impl_signatures),
        diagnostics,
        _ast_effect_metadata(program),
    )


def _assert_matches_legacy(
    source: str, *, modules: dict[str, Program] | None = None
) -> _Snapshot:
    candidate = _snapshot(source, modules=modules)
    legacy = _snapshot(source, checker=_LegacyChecker(), modules=modules)
    assert candidate == legacy
    return candidate


def _pure_padding(count: int) -> str:
    return "\n".join(
        f'@untested("effect scheduler fixture")\n'
        f"func pad_{index}() -> Unit\n"
        f"    return ()\n"
        f"end func pad_{index}\n"
        for index in range(count)
    )


def _chain_source(size: int, *, provider_first: bool = False) -> str:
    definitions = []
    for index in range(size):
        body = (
            '    print("effect seed")\n'
            if index == size - 1
            else f"    f{index + 1}()\n"
        )
        definitions.append(
            f'@untested("effect scheduler fixture")\n'
            f"func f{index}() -> Unit\n"
            f"{body}"
            f"    return ()\n"
            f"end func f{index}\n"
        )
    if provider_first:
        definitions.reverse()
    return "\n".join(definitions)


def _independent_source(size: int, *, effectful: bool) -> str:
    effect = '    print("effect seed")\n' if effectful else ""
    return "\n".join(
        f'@untested("effect scheduler fixture")\n'
        f"func f{index}() -> Unit\n"
        f"{effect}"
        f"    return ()\n"
        f"end func f{index}\n"
        for index in range(size)
    )


def _scc_source(size: int) -> str:
    return "\n".join(
        f'@untested("effect scheduler fixture")\n'
        f"func f{index}() -> Unit\n"
        f"    f{(index + 1) % size}()\n"
        f"{'    print(0)' if index == size - 1 else ''}\n"
        f"    return ()\n"
        f"end func f{index}\n"
        for index in range(size)
    )


def _fan_in_source(size: int) -> str:
    root_calls = "".join(f"    p{index}()\n" for index in range(size - 1))
    definitions = [
        f'@untested("high fan-in fixture")\n'
        f"func root() -> Unit\n"
        f"{root_calls}"
        f"    return ()\n"
        f"end func root\n"
    ]
    for index in range(size - 1):
        body = (
            '    print("effect seed")\n'
            if index == size - 2
            else f"    p{index + 1}()\n"
        )
        definitions.append(
            f'@untested("high fan-in fixture")\n'
            f"func p{index}() -> Unit\n"
            f"{body}"
            f"    return ()\n"
            f"end func p{index}\n"
        )
    return "\n".join(definitions)


@contextmanager
def _record_sparse_runs() -> Iterator[list[bool]]:
    original_run = effect_stabilizer_module._EffectSparseScheduler.run
    calls: list[bool] = []

    def recording_run(self: object) -> None:
        calls.append(True)
        original_run(self)  # type: ignore[arg-type]

    effect_stabilizer_module._EffectSparseScheduler.run = recording_run  # type: ignore[method-assign]
    try:
        yield calls
    finally:
        effect_stabilizer_module._EffectSparseScheduler.run = original_run  # type: ignore[method-assign]


@contextmanager
def _record_legacy_finishes() -> Iterator[list[bool]]:
    original_finish = effect_stabilizer_module._finish_legacy
    calls: list[bool] = []

    def recording_finish(
        checker: TypeChecker,
        functions: list[FunctionDef],
        impl_methods: list[tuple[ImplDef, FunctionDef]],
    ) -> None:
        calls.append(True)
        original_finish(checker, functions, impl_methods)

    effect_stabilizer_module._finish_legacy = recording_finish
    try:
        yield calls
    finally:
        effect_stabilizer_module._finish_legacy = original_finish


def test_small_program_does_not_import_large_scheduler() -> None:
    source = _independent_source(1, effectful=False)
    script = (
        "import sys\n"
        "from geno.parser import parse\n"
        "from geno.typechecker import TypeChecker\n"
        f"TypeChecker().check_program(parse({source!r}))\n"
        "assert 'geno.effect_stabilizer' not in sys.modules\n"
    )
    subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
    )


def test_threshold_keeps_63_exact_and_switches_sparse_64() -> None:
    with _record_sparse_runs() as calls:
        _snapshot(_chain_source(63))
        assert not calls
        _snapshot(_chain_source(64))
        assert calls == [True]


@pytest.mark.parametrize(
    "source",
    [
        _independent_source(64, effectful=False),
        _independent_source(64, effectful=True),
        _chain_source(64, provider_first=True),
    ],
    ids=["pure", "effectful", "provider-first"],
)
def test_dense_or_unchanged_large_programs_do_not_build_sparse_graph(
    source: str,
) -> None:
    with _record_sparse_runs() as calls:
        _snapshot(source)
    assert not calls


@pytest.mark.parametrize("size", [1, 10, 100])
@pytest.mark.parametrize("effectful", [False, True])
def test_common_independent_programs_match_legacy(size: int, effectful: bool) -> None:
    _assert_matches_legacy(_independent_source(size, effectful=effectful))


@pytest.mark.parametrize(
    "source",
    [_chain_source(64), _scc_source(64)],
    ids=["forward-chain", "scc"],
)
def test_long_graphs_match_legacy(source: str) -> None:
    snapshot = _assert_matches_legacy(source)
    assert ("f0", ("io",)) in snapshot[1]


def test_computed_and_higher_order_calls_match_legacy() -> None:
    source = """
@untested("computed-call fixture")
func computed() -> Int
    let funcs = [target]
    let selected = funcs[0]
    return selected(1)
end func computed

@untested("higher-order fixture")
func higher() -> Int
    let stage = fn(value: Int) do
        return target(value)
    end fn
    return stage(2)
end func higher

@untested("effect seed")
func target(value: Int) -> Int
    print(value)
    return value
end func target
""" + _pure_padding(61)
    snapshot = _assert_matches_legacy(source)
    signatures = dict(snapshot[1])
    assert signatures["computed"] == ("io",)
    assert signatures["higher"] == ("io",)


def test_traits_duplicate_slots_specs_and_shadowing_match_legacy() -> None:
    source = """
type Box = Box(value: Int)
type Bag = Bag(value: Int)

trait Touch
    func touch(self: Self) -> Int
end trait

trait Read
    func touch(self: Self) -> Int
end trait

impl Touch for Box
    func touch(self: Box) -> Int
        example Box(0) -> 0
        seed(self.value)
        return self.value
    end func
end impl

impl Read for Bag
    func touch(self: Bag) -> Int
        example Bag(0) -> 0
        return self.value
    end func
end impl

@untested("trait fixture")
func use_box(box: Box) -> Int
    requires contract_seed(box.value)
    ensures result >= 0
    return touch(box)
end func use_box

@untested("trait fixture")
func use_bag(bag: Bag) -> Int
    return touch(bag)
end func use_bag

@untested("shadowing fixture")
func shadowed(seed: (Int) -> Unit, value: Int) -> Unit
    seed(value)
    return ()
end func shadowed

@untested("effect seed")
func seed(value: Int) -> Unit
    print(value)
    return ()
end func seed

@untested("contract seed")
func contract_seed(value: Int) -> Bool
    print(value)
    return value >= 0
end func contract_seed
""" + _pure_padding(59)
    snapshot = _assert_matches_legacy(source)
    signatures = dict(snapshot[1])
    assert signatures["use_box"] == ("io",)
    assert signatures["use_bag"] == ()
    assert signatures["shadowed"] == ()
    impls = {(trait, target): dict(methods) for trait, target, methods in snapshot[2]}
    assert impls[("Touch", "Box")]["touch"] == ("io",)
    assert impls[("Read", "Bag")]["touch"] == ()


def test_delayed_diagnostic_fallback_restores_exact_failed_ast() -> None:
    source = """
@untested("delayed diagnostic fixture")
func invalid() -> Unit
    f0()
    return ()
end func invalid
""" + _chain_source(63)
    checker = _DelayedDiagnosticChecker()
    with _record_legacy_finishes() as finishes:
        candidate = _snapshot(source, checker=checker)
    legacy = _snapshot(source, checker=_DelayedDiagnosticLegacyChecker())
    assert candidate == legacy
    assert not candidate[0]
    assert finishes == [True]


def test_declared_throw_and_try_effects_match_legacy() -> None:
    source = """
@untested("declared effect fixture")
func fixed(value: Int) -> Int with io
    print(value)
    return value
end func fixed

@untested("throw fixture")
func risky() -> Unit
    print("before throw")
    throw "boom"
end func risky

@untested("try fixture")
func consume() -> Int
    try
        risky()
    catch message: String
        print(message)
    end try
    return fixed(1)
end func consume
""" + _pure_padding(61)
    snapshot = _assert_matches_legacy(source)
    signatures = dict(snapshot[1])
    assert signatures["risky"] == ("io", "throw")
    assert signatures["consume"] == ("io",)


def test_module_qualified_calls_and_module_diagnostics_match_legacy() -> None:
    module_source = _chain_source(64)
    module_program_candidate = parse(module_source)
    module_program_legacy = parse(module_source)
    TypeChecker().check_program(module_program_candidate, is_entrypoint=False)
    _LegacyChecker().check_program(module_program_legacy, is_entrypoint=False)
    main = """
import Effects

func main() -> Unit
    Effects.f0()
    return ()
end func
"""
    candidate = _snapshot(main, modules={"Effects": module_program_candidate})
    legacy = _snapshot(
        main,
        checker=_LegacyChecker(),
        modules={"Effects": module_program_legacy},
    )
    assert candidate == legacy


def _random_graph_source(seed: int) -> str:
    rng = random.Random(seed)
    size = 64
    source_order = list(range(size))
    rng.shuffle(source_order)
    effect_seeds = {index for index in range(size) if rng.random() < 0.06}
    if not effect_seeds:
        effect_seeds.add(rng.randrange(size))

    definitions = []
    for index in source_order:
        targets = [
            target
            for target in range(index + 1, min(size, index + 5))
            if rng.random() < 0.35
        ]
        body = "".join(f"    f{target}()\n" for target in targets)
        if index in effect_seeds:
            body += f"    print({index})\n"
        definitions.append(
            f'@untested("random effect graph")\n'
            f"func f{index}() -> Unit\n"
            f"{body}"
            f"    return ()\n"
            f"end func f{index}\n"
        )
    return "\n".join(definitions)


@pytest.mark.parametrize("seed", range(16))
def test_random_graphs_match_legacy(seed: int) -> None:
    _assert_matches_legacy(_random_graph_source(seed))


@pytest.mark.parametrize("size", [64, 128, 256])
def test_high_fan_in_chain_has_bounded_linear_inferences(size: int) -> None:
    checker = _CountingChecker()
    snapshot = _snapshot(_fan_in_source(size), checker=checker)
    assert snapshot[0]
    assert ("root", ("io",)) in snapshot[1]
    assert checker.stabilization_inferences <= 4 * size


def test_scheduler_state_is_isolated_between_fresh_checkers() -> None:
    source = _chain_source(64)
    first = _CountingChecker()
    second = _CountingChecker()
    assert _snapshot(source, checker=first) == _snapshot(source, checker=second)
    assert first.stabilization_inferences == second.stabilization_inferences
