"""Contract-driven property checks for ``geno test --properties`` (proposal 0017).

Every eligible function of a type-checked, loaded module is called on
generated arguments, and the outcome is judged against the function's own
contracts:

- arguments its top-level ``requires`` rejects are *discarded*: they are not
  in the function's domain;
- an ``ensures`` violation is a *failure*;
- any other panic is a *failure*, unless the function has a ``panics``
  example, which says it panics on purpose;
- running out of a resource limit is a *failure*: ``requires`` is how a
  function says an input is too large.

A failing input is shrunk greedily and reported as Geno source that can be
pasted after ``example``.

Generated arguments are *samples*: small trees that render to Geno source. A
sample is realized by parsing its rendering and evaluating it in the module's
environment, exactly as an example's inputs are, so the printed
counterexample is by construction what ran, and every call gets fresh values.

Everything is deterministic for a given seed. Each function has its own
random stream, keyed by the seed, the logical module name and the function's
qualified name, so filtering or adding functions never changes another
function's inputs. Budgets are counted in interpreter steps; wall time is
only a safety stop, and a result says when it fired.

The interpreter used here is the module's own, created with no capabilities
at all: generated inputs cannot reach the host even if effect inference
missed a gated builtin.
"""

from __future__ import annotations

import fnmatch
import hashlib
import math
import random
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable, Iterator, Literal, Mapping, Sequence

from .ast_nodes import FunctionDef, ImplDef, Program
from .builtins import _format_string_literal
from .execution_limits import (
    DEFAULT_PROPERTY_SEED,
    DEFAULT_PROPERTY_TRIALS,
    DEFAULT_TEST_MAX_STEPS,
    DEFAULT_TEST_TIMEOUT,
    PROPERTY_CALL_MAX_SECONDS,
    PROPERTY_CALL_MAX_STEPS,
    PROPERTY_MAX_ATTEMPTS_PER_TRIAL,
    PROPERTY_MAX_SHRINK_CALLS,
    PROPERTY_SAFETY_STOP_TIMEOUTS,
    PROPERTY_SHRINK_STEP_BUDGET,
    PROPERTY_TRIAL_STEP_BUDGET,
)
from .formatter import _strip_strings_and_line_comments
from .panic_outcome import CALL_FAILURES, classify_call_failure
from .sandbox import SandboxConfig, StepLimitExceeded
from .sandbox import TimeoutError as SandboxTimeout
from .types import (
    ArrayType,
    BoolType,
    FloatType,
    FuncType,
    IntType,
    ListType,
    MapType,
    MutableMapType,
    OptionType,
    ResultType,
    SetType,
    StringType,
    TupleType,
    Type,
    TypeDefInfo,
    UnitType,
    UserType,
    VecType,
    close_bounds,
    type_children,
)
from .values import ContractViolationError

if TYPE_CHECKING:
    from .interpreter import Interpreter
    from .typechecker import TypeChecker

PropertyStatus = Literal["passed", "failed", "gave_up", "incomplete", "skipped"]
FailureKind = Literal["ensures", "panic", "limit"]

# Functions an app or a program's host calls; never checked.
_LIFECYCLE_NAMES = ("main", "init", "update", "render")
# The only inferred effect a checked function may have: it mutates its own
# values. Every argument is freshly built, so nothing it mutates is shared.
_ALLOWED_EFFECTS = frozenset({"mutation"})
# Bounds Int satisfies (types.BUILTIN_BOUNDS, closed under implication).
_INT_BOUNDS = close_bounds(("Num",))

# The size schedule: trial i of T has size round(MAX_SIZE * i / (T - 1)).
MAX_SIZE = 64
# Integers stay within the JavaScript-safe range, so a counterexample is a
# valid input on every backend.
MAX_SAFE_INT = 2**53 - 1
_SMALL_INTS = (0, 1, -1)
_SMALL_FLOATS = (0.0, -0.0, 1.0, -1.0, 0.5)
_SMALL_STRINGS = ("", " ", "0", "a b")
_MAX_STRING_LENGTH = 16
_MAX_COLLECTION_LENGTH = 8
# Explored user types per parameter type before generation gives up (a
# polymorphically recursive ADT instantiates new types without end).
_MAX_REACHABLE_USER_TYPES = 256
_LOWERCASE = "abcdefghijklmnopqrstuvwxyz"
_DIGITS = "0123456789"
_OTHER_PRINTABLE = "".join(
    chr(code)
    for code in range(0x21, 0x7F)
    if not chr(code).isalnum()
    # Every other printable ASCII character, including '"' and '\'.
)

_INFINITY = math.inf

# Wall time, for the safety stop only (a module attribute so tests can
# substitute a clock).
_clock = time.monotonic


# =============================================================================
# Configuration and results
# =============================================================================


@dataclass(frozen=True)
class PropertyConfig:
    """How many trials to run and how much work each may take.

    ``timeout`` and ``max_steps`` are ``geno test``'s ``--timeout`` and
    ``--max-steps``; a property call's own allowance is the smaller of those
    and the per-call constants. The budget fields default to the constants
    in ``execution_limits`` and exist so tests can shrink them.
    """

    trials: int = DEFAULT_PROPERTY_TRIALS
    seed: int = DEFAULT_PROPERTY_SEED
    timeout: float | None = DEFAULT_TEST_TIMEOUT
    max_steps: int | None = DEFAULT_TEST_MAX_STEPS
    filter_pattern: str | None = None
    call_max_steps: int = PROPERTY_CALL_MAX_STEPS
    call_max_seconds: float = PROPERTY_CALL_MAX_SECONDS
    trial_step_budget: int = PROPERTY_TRIAL_STEP_BUDGET
    shrink_step_budget: int = PROPERTY_SHRINK_STEP_BUDGET
    max_shrink_calls: int = PROPERTY_MAX_SHRINK_CALLS

    def __post_init__(self) -> None:
        if self.trials < 1:
            raise ValueError("PropertyConfig.trials must be at least 1")

    @property
    def call_steps(self) -> int:
        """The step allowance of one property call."""
        if self.max_steps is None:
            return self.call_max_steps
        return min(self.call_max_steps, self.max_steps)

    @property
    def call_seconds(self) -> float:
        """The deadline of one property call."""
        if self.timeout is None:
            return self.call_max_seconds
        return min(self.call_max_seconds, self.timeout)

    @property
    def safety_stop_seconds(self) -> float:
        """Wall time one function's checks may take before they are stopped."""
        timeout = self.timeout if self.timeout is not None else DEFAULT_TEST_TIMEOUT
        return PROPERTY_SAFETY_STOP_TIMEOUTS * timeout


@dataclass(frozen=True)
class PropertyFailure:
    """How a property failed."""

    kind: FailureKind
    # The runtime message: the contract violation, the panic, or the limit.
    message: str
    # For an ensures failure: the clause's source text (when the source is
    # known) and the interpreter's display of the returned value.
    clause: str | None = None
    result: str | None = None
    # For a limit: which one ("step limit", "timeout", ...).
    limit: str | None = None
    # One line for a text report, e.g. "ensures result >= 0 failed (result
    # was -1)" or "hit the step limit (200,000 steps per property call)".
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "message": self.message,
            "clause": self.clause,
            "result": self.result,
            "limit": self.limit,
        }


@dataclass(frozen=True)
class Counterexample:
    """The shrunk failing arguments, rendered as an example's inputs."""

    arguments: str
    original: str
    shrink_steps: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "arguments": self.arguments,
            "original": self.original,
            "shrink_steps": self.shrink_steps,
        }


@dataclass(frozen=True)
class PropertyResult:
    """The property check of one function."""

    path: str
    function: str
    line: int | None
    status: PropertyStatus
    trials_run: int = 0
    discarded: int = 0
    failure: PropertyFailure | None = None
    counterexample: Counterexample | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "function": self.function,
            "line": self.line,
            "status": self.status,
            "trials_run": self.trials_run,
            "discarded": self.discarded,
            "failure": self.failure.to_dict() if self.failure is not None else None,
            "counterexample": (
                self.counterexample.to_dict()
                if self.counterexample is not None
                else None
            ),
            "note": self.note,
        }


_STATUSES: tuple[PropertyStatus, ...] = (
    "passed",
    "failed",
    "gave_up",
    "incomplete",
    "skipped",
)


@dataclass
class PropertyReport:
    """Every function's property result, with the settings that produced them."""

    seed: int
    trials: int
    results: list[PropertyResult] = field(default_factory=list)

    def count(self, status: PropertyStatus) -> int:
        return sum(1 for result in self.results if result.status == status)

    @property
    def success(self) -> bool:
        """False when a property failed; warnings do not fail a run."""
        return self.count("failed") == 0

    def to_dict(self) -> dict[str, Any]:
        summary: dict[str, Any] = {"seed": self.seed, "trials": self.trials}
        for status in _STATUSES:
            summary[status] = self.count(status)
        summary["functions"] = [result.to_dict() for result in self.results]
        return summary


# =============================================================================
# Samples
# =============================================================================

SampleKind = Literal[
    "int",
    "float",
    "bool",
    "string",
    "unit",
    "tuple",
    "list",
    "set",
    "vec",
    "array",
    "map",
    "entry",
    "ctor",
]

# Collections rendered as a builtin call around a list literal.
_LIST_BUILDERS: dict[str, str] = {
    "set": "set_from_list",
    "vec": "vec_from_list",
    "array": "array_from_list",
}


@dataclass(frozen=True)
class Sample:
    """A generated value as a tree that renders to Geno source.

    ``value`` holds a leaf's Python value, ``name`` a constructor's name
    (``Some``, ``None``, ``Ok`` and ``Err`` included) and ``items`` the
    children: elements, fields, or a map's ``entry`` pairs.
    """

    kind: SampleKind
    type_: Type
    value: int | float | bool | str | None = None
    name: str | None = None
    items: tuple[Sample, ...] = ()


def render(sample: Sample) -> str:
    """Geno source for *sample*: a literal, a constructor or a builder call."""
    kind = sample.kind
    if kind == "int":
        return str(sample.value)
    if kind == "float":
        assert isinstance(sample.value, float)
        return render_float(sample.value)
    if kind == "bool":
        return "true" if sample.value else "false"
    if kind == "string":
        assert isinstance(sample.value, str)
        return _format_string_literal(sample.value)
    if kind == "unit":
        return "()"
    if kind == "tuple":
        return "(" + ", ".join(render(item) for item in sample.items) + ")"
    if kind == "list":
        return "[" + ", ".join(render(item) for item in sample.items) + "]"
    if kind in _LIST_BUILDERS:
        elements = ", ".join(render(item) for item in sample.items)
        return f"{_LIST_BUILDERS[kind]}([{elements}])"
    if kind == "map":
        return "{" + ", ".join(render(item) for item in sample.items) + "}"
    if kind == "entry":
        key, value = sample.items
        return f"{render(key)}: {render(value)}"
    assert sample.name is not None
    if not sample.items:
        return sample.name
    return f"{sample.name}(" + ", ".join(render(item) for item in sample.items) + ")"


def render_float(value: float) -> str:
    """A float as a positional decimal with a '.', never with an exponent."""
    text = repr(value)
    if "e" in text or "E" in text:
        text = format(Decimal(text), "f")
    if "." not in text:
        text += ".0"
    return text


def render_arguments(samples: Sequence[Sample]) -> str:
    """Arguments as an example's inputs: ``a`` for one, ``a, b`` for several."""
    if not samples:
        return "()"
    return ", ".join(render(sample) for sample in samples)


def _sample_key(sample: Sample) -> object:
    """A hashable stand-in for the value *sample* realizes, to deduplicate keys.

    Equal values get equal keys (``0.0`` and ``-0.0`` among them), and the
    kind is part of the key, so ``true`` and ``1`` stay apart.
    """
    value = sample.value
    if isinstance(value, float):
        value = value + 0.0  # -0.0 and 0.0 are one key
    return (
        sample.kind,
        value,
        sample.name,
        tuple(_sample_key(item) for item in sample.items),
    )


def _dedupe(
    items: Sequence[Sample], key: Callable[[Sample], object]
) -> tuple[Sample, ...]:
    """Drop later items whose key repeats, keeping generation order."""
    first: dict[object, Sample] = {}
    for item in items:
        first.setdefault(key(item), item)
    return tuple(first.values())


def _entry_key(entry: Sample) -> object:
    return _sample_key(entry.items[0])


# =============================================================================
# Generators
# =============================================================================


class Generators:
    """Generate, minimize and shrink samples of a module's types."""

    def __init__(self, type_defs: Mapping[str, TypeDefInfo]) -> None:
        self._type_defs = type_defs
        self._depths: dict[Type, float] = {}
        self._variant_cache: dict[UserType, list[tuple[str, list[Type]]]] = {}
        self._choice_cache: dict[
            tuple[UserType, bool], list[tuple[str, list[Type]]]
        ] = {}

    # -- which types have a generator -------------------------------------

    def unsupported(self, type_: Type) -> Type | None:
        """The innermost part of *type_* that has no generator, if any."""
        if isinstance(type_, (IntType, FloatType, BoolType, StringType, UnitType)):
            return None
        if isinstance(type_, (ListType, SetType, VecType, ArrayType)):
            return self.unsupported(type_.element_type)
        if isinstance(type_, MapType):
            return self.unsupported(type_.key_type) or self.unsupported(
                type_.value_type
            )
        if isinstance(type_, OptionType):
            return self.unsupported(type_.value_type)
        if isinstance(type_, ResultType):
            return self.unsupported(type_.ok_type) or self.unsupported(type_.err_type)
        if isinstance(type_, TupleType):
            for element in type_.element_types:
                missing = self.unsupported(element)
                if missing is not None:
                    return missing
            return None
        if isinstance(type_, UserType):
            if type_.name not in self._type_defs:
                return type_
            return type_ if self.min_depth(type_) == _INFINITY else None
        # Function types, Async, MutableMap, Any, Never, modules and type
        # variables have no generator.
        return type_

    def min_depth(self, type_: Type) -> float:
        """Constructor nesting the smallest value of *type_* needs (inf: none)."""
        if isinstance(type_, UserType):
            if type_ not in self._depths:
                self._solve_depths(type_)
            return self._depths.get(type_, _INFINITY)
        return self._depth_with(type_, self._depths, explore=True)

    def _variants(self, type_: UserType) -> list[tuple[str, list[Type]]]:
        """The variants of *type_* with its type arguments substituted."""
        cached = self._variant_cache.get(type_)
        if cached is not None:
            return cached
        from .typechecker import TypeChecker

        info = self._type_defs[type_.name]
        substitutions: dict[str, Type] = dict(zip(info.type_params, type_.type_args))
        variants = [
            (
                name,
                [
                    TypeChecker._apply_substitutions(field_type, substitutions)
                    for _field_name, field_type in fields
                ],
            )
            for name, fields in info.variants.items()
        ]
        self._variant_cache[type_] = variants
        return variants

    def _solve_depths(self, root: UserType) -> None:
        """Least fixpoint of min_depth over the user types *root* reaches."""
        reachable: list[UserType] = []
        seen: set[Type] = set()
        pending: list[Type] = [root]
        while pending and len(reachable) < _MAX_REACHABLE_USER_TYPES:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            if isinstance(current, UserType):
                if current.name not in self._type_defs or current in self._depths:
                    continue
                reachable.append(current)
                for _name, fields in self._variants(current):
                    pending.extend(fields)
            else:
                pending.extend(_children(current))

        depths: dict[Type, float] = dict(self._depths)
        for user_type in reachable:
            depths[user_type] = _INFINITY
        changed = True
        while changed:
            changed = False
            for user_type in reachable:
                best = _INFINITY
                for _name, fields in self._variants(user_type):
                    variant = 1 + max(
                        (
                            self._depth_with(field_type, depths, explore=False)
                            for field_type in fields
                        ),
                        default=0,
                    )
                    best = min(best, variant)
                if best < depths[user_type]:
                    depths[user_type] = best
                    changed = True
        for user_type in reachable:
            self._depths[user_type] = depths[user_type]

    def _depth_with(
        self, type_: Type, depths: Mapping[Type, float], *, explore: bool
    ) -> float:
        """min_depth of *type_* given the user-type depths in *depths*.

        With *explore*, a user type not yet solved is solved; without it
        (inside a fixpoint), it was left out of the explored set by the
        exploration cap and counts as unconstructible.
        """

        def inner(part: Type) -> float:
            return self._depth_with(part, depths, explore=explore)

        if isinstance(type_, (IntType, FloatType, BoolType, StringType, UnitType)):
            return 0
        if isinstance(type_, (ListType, SetType, VecType, ArrayType)):
            # Empty is always possible, but elements must be generable.
            return 0 if inner(type_.element_type) < _INFINITY else _INFINITY
        if isinstance(type_, MapType):
            key = inner(type_.key_type)
            value = inner(type_.value_type)
            return 0 if max(key, value) < _INFINITY else _INFINITY
        if isinstance(type_, OptionType):
            return 0 if inner(type_.value_type) < _INFINITY else _INFINITY
        if isinstance(type_, ResultType):
            ok = inner(type_.ok_type)
            err = inner(type_.err_type)
            # Both sides must be generable, since either may be chosen.
            return min(ok, err) if max(ok, err) < _INFINITY else _INFINITY
        if isinstance(type_, TupleType):
            return max((inner(element) for element in type_.element_types), default=0)
        if isinstance(type_, UserType):
            if type_ in depths:
                return depths[type_]
            if explore and type_.name in self._type_defs:
                return self.min_depth(type_)
            return _INFINITY
        return _INFINITY

    # -- generation --------------------------------------------------------

    def generate(self, rng: random.Random, type_: Type, size: int) -> Sample:
        """A random sample of *type_* at *size* (0 to MAX_SIZE)."""
        return self._generate(rng, type_, size, 0, 1 + size // 16)

    def _generate(
        self,
        rng: random.Random,
        type_: Type,
        size: int,
        depth: int,
        max_depth: int,
    ) -> Sample:
        bounded = depth >= max_depth
        if isinstance(type_, IntType):
            return Sample("int", type_, _generate_int(rng, size))
        if isinstance(type_, FloatType):
            return Sample("float", type_, _generate_float(rng, size))
        if isinstance(type_, BoolType):
            return Sample("bool", type_, rng.random() < 0.5)
        if isinstance(type_, StringType):
            return Sample("string", type_, _generate_string(rng, size))
        if isinstance(type_, UnitType):
            return Sample("unit", type_)
        if isinstance(type_, TupleType):
            return Sample(
                "tuple",
                type_,
                items=tuple(
                    self._generate(rng, element, size, depth, max_depth)
                    for element in type_.element_types
                ),
            )
        if isinstance(type_, (ListType, SetType, VecType, ArrayType)):
            kind = _collection_kind(type_)
            length = 0 if bounded else rng.randint(0, _collection_length(size))
            elements = [
                self._generate(rng, type_.element_type, size // 2, depth, max_depth)
                for _ in range(length)
            ]
            if kind == "set":
                return Sample(kind, type_, items=_dedupe(elements, _sample_key))
            return Sample(kind, type_, items=tuple(elements))
        if isinstance(type_, MapType):
            length = 0 if bounded else rng.randint(0, _collection_length(size))
            entry_type = TupleType((type_.key_type, type_.value_type))
            entries = [
                Sample(
                    "entry",
                    entry_type,
                    items=(
                        self._generate(
                            rng, type_.key_type, size // 2, depth, max_depth
                        ),
                        self._generate(
                            rng, type_.value_type, size // 2, depth, max_depth
                        ),
                    ),
                )
                for _ in range(length)
            ]
            return Sample("map", type_, items=_dedupe(entries, _entry_key))
        if isinstance(type_, OptionType):
            if bounded or rng.random() < 0.2:
                return Sample("ctor", type_, name="None")
            inner = self._generate(rng, type_.value_type, size, depth, max_depth)
            return Sample("ctor", type_, name="Some", items=(inner,))
        if isinstance(type_, ResultType):
            if bounded:
                ok = self.min_depth(type_.ok_type) <= self.min_depth(type_.err_type)
            else:
                ok = rng.random() < 0.6
            if ok:
                inner = self._generate(rng, type_.ok_type, size, depth, max_depth)
                return Sample("ctor", type_, name="Ok", items=(inner,))
            inner = self._generate(rng, type_.err_type, size, depth, max_depth)
            return Sample("ctor", type_, name="Err", items=(inner,))
        if isinstance(type_, UserType):
            variants = self._constructible_variants(type_, minimal_only=bounded)
            name, fields = variants[rng.randrange(len(variants))]
            return Sample(
                "ctor",
                type_,
                name=name,
                items=tuple(
                    self._generate(rng, field_type, size, depth + 1, max_depth)
                    for field_type in fields
                ),
            )
        raise ValueError(f"No generator for {type_}")

    def _constructible_variants(
        self, type_: UserType, *, minimal_only: bool
    ) -> list[tuple[str, list[Type]]]:
        """The variants that have a finite value, or only the smallest ones.

        Kept in definition order (a list, never a set), so a random choice
        among them depends only on the seed.
        """
        key = (type_, minimal_only)
        cached = self._choice_cache.get(key)
        if cached is not None:
            return cached
        options: list[tuple[float, str, list[Type]]] = []
        for name, fields in self._variants(type_):
            depth = 1 + max((self.min_depth(f) for f in fields), default=0)
            if depth < _INFINITY:
                options.append((depth, name, fields))
        if minimal_only:
            least = min(depth for depth, _name, _fields in options)
            options = [option for option in options if option[0] == least]
        choices = [(name, fields) for _depth, name, fields in options]
        self._choice_cache[key] = choices
        return choices

    def minimal(self, type_: Type) -> Sample:
        """The smallest sample of *type_*: 0, "", empty, None, a nullary variant."""
        if isinstance(type_, IntType):
            return Sample("int", type_, 0)
        if isinstance(type_, FloatType):
            return Sample("float", type_, 0.0)
        if isinstance(type_, BoolType):
            return Sample("bool", type_, False)
        if isinstance(type_, StringType):
            return Sample("string", type_, "")
        if isinstance(type_, UnitType):
            return Sample("unit", type_)
        if isinstance(type_, TupleType):
            return Sample(
                "tuple",
                type_,
                items=tuple(self.minimal(element) for element in type_.element_types),
            )
        if isinstance(type_, (ListType, SetType, VecType, ArrayType)):
            return Sample(_collection_kind(type_), type_)
        if isinstance(type_, MapType):
            return Sample("map", type_)
        if isinstance(type_, OptionType):
            return Sample("ctor", type_, name="None")
        if isinstance(type_, ResultType):
            if self.min_depth(type_.ok_type) <= self.min_depth(type_.err_type):
                return Sample(
                    "ctor", type_, name="Ok", items=(self.minimal(type_.ok_type),)
                )
            return Sample(
                "ctor", type_, name="Err", items=(self.minimal(type_.err_type),)
            )
        if isinstance(type_, UserType):
            name, fields = self._constructible_variants(type_, minimal_only=True)[0]
            return Sample(
                "ctor",
                type_,
                name=name,
                items=tuple(self.minimal(field_type) for field_type in fields),
            )
        raise ValueError(f"No generator for {type_}")

    # -- shrinking ---------------------------------------------------------

    def shrink(self, sample: Sample) -> Iterator[Sample]:
        """Smaller candidates for *sample*, most promising first."""
        kind = sample.kind
        if kind == "int":
            assert isinstance(sample.value, int)
            for value in _shrink_int(sample.value):
                yield Sample("int", sample.type_, value)
        elif kind == "float":
            assert isinstance(sample.value, float)
            for fraction in _shrink_float(sample.value):
                yield Sample("float", sample.type_, fraction)
        elif kind == "bool":
            if sample.value:
                yield Sample("bool", sample.type_, False)
        elif kind == "string":
            assert isinstance(sample.value, str)
            for text in _shrink_string(sample.value):
                yield Sample("string", sample.type_, text)
        elif kind in ("list", "set", "vec", "array", "map"):
            yield from self._shrink_collection(sample)
        elif kind in ("tuple", "entry"):
            yield from self._shrink_fields(sample)
        elif kind == "ctor":
            yield from self._shrink_constructor(sample)

    def _shrink_collection(self, sample: Sample) -> Iterator[Sample]:
        items = sample.items
        if not items:
            return

        def rebuilt(new_items: Sequence[Sample]) -> Sample:
            if sample.kind == "set":
                new_items = _dedupe(new_items, _sample_key)
            elif sample.kind == "map":
                new_items = _dedupe(new_items, _entry_key)
            return Sample(sample.kind, sample.type_, items=tuple(new_items))

        yield rebuilt(())
        count = len(items)
        if count > 1:
            yield rebuilt(items[count // 2 :])
            yield rebuilt(items[: count // 2])
        for index in range(count):
            yield rebuilt(items[:index] + items[index + 1 :])
        for index, item in enumerate(items):
            for smaller in self.shrink(item):
                yield rebuilt(items[:index] + (smaller,) + items[index + 1 :])

    def _shrink_fields(self, sample: Sample) -> Iterator[Sample]:
        items = sample.items
        for index, item in enumerate(items):
            for smaller in self.shrink(item):
                yield Sample(
                    sample.kind,
                    sample.type_,
                    name=sample.name,
                    items=items[:index] + (smaller,) + items[index + 1 :],
                )

    def _shrink_constructor(self, sample: Sample) -> Iterator[Sample]:
        if isinstance(sample.type_, OptionType):
            if sample.name == "Some":
                yield Sample("ctor", sample.type_, name="None")
            yield from self._shrink_fields(sample)
            return
        if isinstance(sample.type_, UserType):
            smallest = self.minimal(sample.type_)
            if smallest != sample:
                yield smallest
            for item in sample.items:
                if item.type_ == sample.type_:
                    yield item
        yield from self._shrink_fields(sample)


def _children(type_: Type) -> tuple[Type, ...]:
    parts = type_children(type_)
    return parts[0] if parts is not None else ()


def _collection_kind(type_: Type) -> SampleKind:
    if isinstance(type_, SetType):
        return "set"
    if isinstance(type_, VecType):
        return "vec"
    if isinstance(type_, ArrayType):
        return "array"
    return "list"


def _collection_length(size: int) -> int:
    return min(1 + size // 8, _MAX_COLLECTION_LENGTH)


def _magnitude(size: int) -> int:
    return int(2 ** (1 + size // 6))


def _generate_int(rng: random.Random, size: int) -> int:
    roll = rng.random()
    if roll < 0.15:
        return _SMALL_INTS[rng.randrange(len(_SMALL_INTS))]
    if roll < 0.16:
        return MAX_SAFE_INT
    if roll < 0.17:
        return -MAX_SAFE_INT
    bound = _magnitude(size)
    return rng.randint(-bound, bound)


def _generate_float(rng: random.Random, size: int) -> float:
    if rng.random() < 0.15:
        return _SMALL_FLOATS[rng.randrange(len(_SMALL_FLOATS))]
    bound = _magnitude(size)
    # Uniform in (-bound, bound), at a random precision so counterexamples
    # stay readable; always finite.
    value = rng.uniform(-bound, bound)
    return round(value, rng.randrange(7))


def _generate_string(rng: random.Random, size: int) -> str:
    if rng.random() < 0.10:
        return _SMALL_STRINGS[rng.randrange(len(_SMALL_STRINGS))]
    length = rng.randint(0, min(size // 4, _MAX_STRING_LENGTH))
    chars: list[str] = []
    for _ in range(length):
        roll = rng.random()
        if roll < 0.40:
            pool = _LOWERCASE
        elif roll < 0.60:
            pool = _DIGITS
        elif roll < 0.75:
            pool = " "
        elif roll < 0.80:
            pool = "\t\n"
        else:
            pool = _OTHER_PRINTABLE
        chars.append(pool[rng.randrange(len(pool))])
    return "".join(chars)


def _toward_zero(value: int, divisor: int) -> int:
    quotient = abs(value) // divisor
    return quotient if value >= 0 else -quotient


def _shrink_int(value: int) -> Iterator[int]:
    """Candidates in increasing magnitude, then one step toward 0.

    0; then value/2**k for k = K, ..., 2 (the smallest first); then value
    minus value/2**k (k = 1, 2, ...), from value/2 up to value; then value
    one closer to 0. Trying the small fractions first matters when a failing
    call is expensive: a limit failure found at ``MAX_SAFE_INT`` costs a full
    call allowance for every failing candidate, so halving one step at a time
    would spend the shrink budget about 20 halvings in, far from the
    threshold. Here the small candidates pass cheaply, and when every input
    from some threshold up fails, the first candidate that fails is less
    than twice that threshold.
    """
    if value == 0:
        return
    offered = {value}
    candidates = [0]
    fractions: list[int] = []
    divisor = 4
    while True:
        fraction = _toward_zero(value, divisor)
        if fraction == 0:
            break
        fractions.append(fraction)
        divisor *= 2
    candidates.extend(reversed(fractions))
    divisor = 2
    while True:
        step = _toward_zero(value, divisor)
        if step == 0:
            break
        candidates.append(value - step)
        divisor *= 2
    candidates.append(value - 1 if value > 0 else value + 1)
    for candidate in candidates:
        if candidate not in offered:
            offered.add(candidate)
            yield candidate


def _shrink_float(value: float) -> Iterator[float]:
    """0.0, the integer part, half (for |x| >= 2), fewer decimals."""
    if value == 0.0:
        return
    offered = {render_float(value)}
    candidates = [0.0, float(math.trunc(value))]
    if abs(value) >= 2:
        candidates.append(value / 2)
    text = render_float(value)
    decimals = len(text.split(".", 1)[1])
    candidates.extend(round(value, places) for places in range(decimals - 1, 0, -1))
    for candidate in candidates:
        shown = render_float(candidate)
        if math.isfinite(candidate) and shown not in offered:
            offered.add(shown)
            yield candidate


def _shrink_string(text: str) -> Iterator[str]:
    """ "", either half, one character fewer, a character replaced with 'a'."""
    if not text:
        return
    offered = {text}
    candidates = [""]
    length = len(text)
    if length > 1:
        candidates.extend((text[length // 2 :], text[: length // 2]))
    candidates.extend(text[:index] + text[index + 1 :] for index in range(length))
    candidates.extend(
        text[:index] + "a" + text[index + 1 :]
        for index in range(length)
        if text[index] != "a"
    )
    for candidate in candidates:
        if candidate not in offered:
            offered.add(candidate)
            yield candidate


# =============================================================================
# Targets: the functions to check
# =============================================================================


@dataclass
class PropertyTarget:
    """A function to check, or to report as skipped."""

    name: str  # qualified: "f", or "Target.Trait.method" for an impl method
    base_name: str
    target_method_name: str  # "Target.method" for an impl method, for --filter
    definition: FunctionDef
    param_types: tuple[Type, ...] = ()
    impl: tuple[str, str] | None = None  # (trait, target)
    note: str | None = None  # set when the function is skipped

    @property
    def has_panics_example(self) -> bool:
        return any(example.expects_panic for example in self.definition.specs.examples)

    def matches(self, pattern: str | None) -> bool:
        if pattern is None:
            return True
        return (
            fnmatch.fnmatch(self.name, pattern)
            or fnmatch.fnmatch(self.target_method_name, pattern)
            or fnmatch.fnmatch(self.base_name, pattern)
        )


def property_targets(
    program: Program,
    checker: TypeChecker,
    generators: Generators | None = None,
    *,
    filter_pattern: str | None = None,
) -> list[PropertyTarget]:
    """The functions of *program* that property checks cover, in source order.

    Functions out of scope are left out: ``main`` and the app lifecycle hooks,
    async and ``@untested`` functions, functions with an effect other than
    ``mutation``, functions without parameters, and those *filter_pattern*
    does not match. A function in scope whose parameter types have no
    generator, or whose type parameters ``Int`` cannot instantiate, is
    returned with a ``note`` saying why it is skipped.
    """
    generators = generators or Generators(checker.type_defs)
    targets: list[PropertyTarget] = []
    for definition in program.definitions:
        if isinstance(definition, FunctionDef):
            if definition.name in _LIFECYCLE_NAMES:
                continue
            func_type = checker.global_env.lookup(definition.name)
            target = _target(
                definition,
                func_type,
                checker,
                generators,
                name=definition.name,
                base_name=definition.name,
                target_method_name=definition.name,
            )
        elif isinstance(definition, ImplDef):
            methods = checker.impl_registry.get(
                (definition.trait_name, definition.target_type), {}
            )
            for method in definition.methods:
                qualified = (
                    f"{definition.target_type}.{definition.trait_name}.{method.name}"
                )
                target = _target(
                    method,
                    methods.get(method.name),
                    checker,
                    generators,
                    name=qualified,
                    base_name=method.name,
                    target_method_name=f"{definition.target_type}.{method.name}",
                    impl=(definition.trait_name, definition.target_type),
                )
                if target is not None and target.matches(filter_pattern):
                    targets.append(target)
            continue
        else:
            continue
        if target is not None and target.matches(filter_pattern):
            targets.append(target)
    return targets


def _target(
    definition: FunctionDef,
    func_type: Type | None,
    checker: TypeChecker,
    generators: Generators,
    *,
    name: str,
    base_name: str,
    target_method_name: str,
    impl: tuple[str, str] | None = None,
) -> PropertyTarget | None:
    if definition.is_async or definition.untested_reason is not None:
        return None
    if not isinstance(func_type, FuncType) or not definition.params:
        return None
    if not func_type.effects <= _ALLOWED_EFFECTS:
        return None
    target = PropertyTarget(
        name=name,
        base_name=base_name,
        target_method_name=target_method_name,
        definition=definition,
        impl=impl,
    )

    from .typechecker import TypeChecker

    substitutions: dict[str, Type] = {}
    for type_param in definition.type_params:
        bounds = definition.type_param_bounds.get(type_param, [])
        missing = [
            bound
            for bound in sorted(close_bounds(bounds))
            if bound not in _INT_BOUNDS and (bound, "Int") not in checker.impl_registry
        ]
        if missing:
            target.note = (
                f"type parameter {type_param}: {' + '.join(missing)} cannot be "
                "instantiated with Int"
            )
            return target
        substitutions[type_param] = IntType()

    param_types = tuple(
        TypeChecker._apply_substitutions(param_type, substitutions)
        for param_type in func_type.param_types
    )
    for param, param_type in zip(definition.params, param_types):
        missing_type = generators.unsupported(param_type)
        if missing_type is not None:
            target.note = _no_generator_note(param.name, param_type, missing_type)
            return target
    target.param_types = param_types
    return target


def _no_generator_note(param: str, param_type: Type, missing: Type) -> str:
    if isinstance(missing, UserType):
        return f"parameter {param}: type {missing} has no value to generate"
    if isinstance(missing, MutableMapType):
        return (
            f"parameter {param}: {missing} has no generator (it has no "
            "one-expression literal to print)"
        )
    return f"parameter {param}: {missing} has no generator"


# =============================================================================
# Running the checks
# =============================================================================

OutcomeKind = Literal[
    "pass",
    "discard",
    "ensures",
    "panic",
    "limit",
    "budget",
    "refused",
    "unrealizable",
]


@dataclass(frozen=True)
class _Outcome:
    kind: OutcomeKind
    steps: int = 0
    message: str = ""
    clause_index: int | None = None
    result: str | None = None
    limit: str | None = None

    @property
    def is_failure(self) -> bool:
        return self.kind in ("ensures", "panic", "limit")

    def same_failure(self, other: _Outcome) -> bool:
        """Whether *other* fails the way this outcome does, for shrinking."""
        if self.kind != other.kind:
            return False
        if self.kind == "ensures":
            return self.clause_index == other.clause_index
        return True


class _Stop(Exception):
    """A phase ran out of steps, calls or wall time."""

    def __init__(self, note: str) -> None:
        super().__init__(note)
        self.note = note


def _stream_seed(seed: int, module: str, name: str) -> int:
    digest = hashlib.sha256(f"{seed}:{module}:{name}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def trial_size(index: int, trials: int) -> int:
    """The size of trial *index* of *trials*: growing from 0 to MAX_SIZE."""
    return round(MAX_SIZE * index / max(1, trials - 1))


def ensures_clause_text(
    source: str | None, definition: FunctionDef, index: int
) -> str | None:
    """The source of ``ensures`` clause *index*, to the end of its line.

    The clause's own location is its ``ensures`` keyword (an expression's
    location can be an inner operand), so the text is the rest of that line
    after the keyword, without a trailing comment.
    """
    if source is None:
        return None
    clauses = definition.specs.ensures
    if not 0 <= index < len(clauses):
        return None
    location = clauses[index].location
    lines = source.split("\n")
    if location is None or not 1 <= location.line <= len(lines):
        return None
    rest = lines[location.line - 1][max(location.column - 1, 0) :]
    if rest.startswith("ensures"):
        rest = rest[len("ensures") :]
    code_length = len(_strip_strings_and_line_comments(rest))
    text = rest[:code_length].strip()
    return text or None


class _FunctionCheck:
    """The trial and shrink phases for one function."""

    def __init__(
        self,
        runner: PropertyRunner,
        target: PropertyTarget,
        func: Any,
    ) -> None:
        self.runner = runner
        self.config = runner.config
        self.target = target
        self.func = func
        self.started = _clock()
        # A seeded, reproducible stream of test inputs, never exposed to Geno
        # code: not a security use of randomness.
        stream = _stream_seed(self.config.seed, runner.module, target.name)
        self.rng = random.Random(stream)  # noqa: S311

    def result(self, status: PropertyStatus, **fields: Any) -> PropertyResult:
        location = self.target.definition.location
        return PropertyResult(
            path=self.runner.path,
            function=self.target.name,
            line=location.line if location is not None else None,
            status=status,
            **fields,
        )

    def _safety_stopped(self) -> bool:
        return _clock() - self.started > self.config.safety_stop_seconds

    def run(self) -> PropertyResult:
        config = self.config
        gens = self.runner.generators
        steps_used = 0
        attempts = 0
        max_attempts = PROPERTY_MAX_ATTEMPTS_PER_TRIAL * config.trials
        trials_run = 0
        discarded = 0
        for index in range(config.trials):
            size = trial_size(index, config.trials)
            while True:
                if attempts >= max_attempts:
                    break
                if self._safety_stopped():
                    return self.result(
                        "incomplete",
                        trials_run=trials_run,
                        discarded=discarded,
                        note=(
                            f"stopped after {trials_run} trials by the "
                            "wall-clock safety stop"
                        ),
                    )
                remaining = config.trial_step_budget - steps_used
                if remaining <= 0:
                    return self._budget_spent(trials_run, discarded)
                attempts += 1
                args = tuple(
                    gens.generate(self.rng, param_type, size)
                    for param_type in self.target.param_types
                )
                outcome = self.runner.call(self, args, remaining)
                steps_used += outcome.steps
                if outcome.kind == "discard":
                    discarded += 1
                    continue
                if outcome.kind == "pass":
                    trials_run += 1
                    break
                if outcome.kind == "budget":
                    return self._budget_spent(trials_run, discarded)
                if outcome.kind in ("refused", "unrealizable"):
                    return self.result(
                        "skipped",
                        trials_run=trials_run,
                        discarded=discarded,
                        note=outcome.message,
                    )
                trials_run += 1
                return self._shrink(args, outcome, trials_run, discarded)
            if attempts >= max_attempts:
                break
        if trials_run < max(1, config.trials // 10):
            return self.result(
                "gave_up",
                trials_run=trials_run,
                discarded=discarded,
                note=(
                    f"gave up: only {trials_run} of {attempts} generated "
                    "inputs satisfied the requires clauses"
                ),
            )
        note = None
        if trials_run < config.trials:
            note = (
                f"{trials_run} of {config.trials} trials ran: the requires "
                f"clauses discarded {discarded} generated inputs"
            )
        return self.result(
            "passed", trials_run=trials_run, discarded=discarded, note=note
        )

    def _budget_spent(self, trials_run: int, discarded: int) -> PropertyResult:
        return self.result(
            "incomplete",
            trials_run=trials_run,
            discarded=discarded,
            note=f"stopped after {trials_run} trials: step budget spent",
        )

    def _shrink(
        self,
        original: tuple[Sample, ...],
        failure: _Outcome,
        trials_run: int,
        discarded: int,
    ) -> PropertyResult:
        config = self.config
        gens = self.runner.generators
        current = original
        current_failure = failure
        steps_used = 0
        calls = 0
        accepted = 0
        seen = {render_arguments(original)}
        note: str | None = None
        try:
            improved = True
            while improved:
                improved = False
                for candidate in _argument_candidates(gens, current):
                    rendered = render_arguments(candidate)
                    if rendered in seen:
                        continue
                    seen.add(rendered)
                    if self._safety_stopped():
                        raise _Stop("shrinking stopped by the wall-clock safety stop")
                    remaining = config.shrink_step_budget - steps_used
                    if remaining <= 0 or calls >= config.max_shrink_calls:
                        raise _Stop("shrinking stopped: shrink budget spent")
                    calls += 1
                    outcome = self.runner.call(self, candidate, remaining)
                    steps_used += outcome.steps
                    if outcome.kind == "budget":
                        raise _Stop("shrinking stopped: shrink budget spent")
                    if outcome.is_failure and current_failure.same_failure(outcome):
                        current = candidate
                        current_failure = outcome
                        accepted += 1
                        improved = True
                        break
        except _Stop as stop:
            note = stop.note
        return self.result(
            "failed",
            trials_run=trials_run,
            discarded=discarded,
            failure=self.runner.failure(self.target, current_failure),
            counterexample=Counterexample(
                arguments=render_arguments(current),
                original=render_arguments(original),
                shrink_steps=accepted,
            ),
            note=note,
        )


def _argument_candidates(
    gens: Generators, args: tuple[Sample, ...]
) -> Iterator[tuple[Sample, ...]]:
    for index, arg in enumerate(args):
        for smaller in gens.shrink(arg):
            yield args[:index] + (smaller,) + args[index + 1 :]


class PropertyRunner:
    """Check the eligible functions of one loaded module.

    *interpreter* must have the module loaded (``execute_main=False``) with
    no capabilities; ``check_module_properties`` builds one.
    """

    def __init__(
        self,
        interpreter: Interpreter,
        checker: TypeChecker,
        *,
        module: str,
        path: str,
        config: PropertyConfig,
        source: str | None = None,
    ) -> None:
        self.interpreter = interpreter
        self.checker = checker
        self.module = module
        self.path = path
        self.config = config
        self.source = source
        self.generators = Generators(checker.type_defs)

    def check(self, program: Program) -> list[PropertyResult]:
        targets = property_targets(
            program,
            self.checker,
            self.generators,
            filter_pattern=self.config.filter_pattern,
        )
        return self.interpreter.run_with_recursion_room(
            lambda: [self._check_target(target) for target in targets]
        )

    def _resolve(self, target: PropertyTarget) -> Any:
        if target.impl is not None:
            return self.interpreter.trait_impls.get(target.impl, {}).get(
                target.base_name
            )
        return self.interpreter.global_env.lookup(target.name)

    def _check_target(self, target: PropertyTarget) -> PropertyResult:
        check = _FunctionCheck(self, target, self._resolve(target))
        if target.note is not None:
            return check.result("skipped", note=target.note)
        if check.func is None:
            return check.result(
                "skipped", note=f"function '{target.name}' is not loaded"
            )
        return check.run()

    # -- one call ----------------------------------------------------------

    def call(
        self, check: _FunctionCheck, args: tuple[Sample, ...], remaining: int
    ) -> _Outcome:
        """Realize *args*, then call the function within a step allowance.

        *remaining* is what is left of the phase's step budget; realizing
        the arguments counts against it too. The call's allowance is the
        per-call allowance, or less when the phase is nearly spent, and a
        step-limit hit counts as a failure only on a full allowance.
        """
        interp = self.interpreter
        call_steps = self.config.call_steps
        before = interp.steps
        values: list[Any] = []
        try:
            with interp.step_allowance(min(call_steps, remaining)):
                with interp._execution_deadline(self.config.call_seconds):
                    for arg in args:
                        values.append(self._realize(arg))
        except CALL_FAILURES as exc:
            return _Outcome(
                "unrealizable",
                steps=interp.steps - before,
                message=(
                    "cannot build the generated input "
                    f"{render_arguments(args)} in this module: "
                    f"{getattr(exc, 'message', None) or exc}"
                ),
            )
        allowance = min(call_steps, remaining - (interp.steps - before))
        if allowance <= 0:
            return _Outcome("budget", steps=interp.steps - before)
        try:
            with interp.step_allowance(allowance):
                with interp._execution_deadline(self.config.call_seconds):
                    interp._call_function(check.func, values)
        except CALL_FAILURES as exc:
            return self._classify(
                exc,
                check.target,
                full=allowance >= call_steps,
                steps=interp.steps - before,
            )
        return _Outcome("pass", steps=interp.steps - before)

    def _realize(self, sample: Sample) -> Any:
        from .parser import parse_expression

        expression = parse_expression(render(sample), "<property input>")
        return self.interpreter.eval_expr(expression, self.interpreter.global_env)

    def _classify(
        self,
        exc: BaseException,
        target: PropertyTarget,
        *,
        full: bool,
        steps: int,
    ) -> _Outcome:
        if isinstance(exc, ContractViolationError) and exc.call_depth == 1:
            if exc.clause == "requires" and exc.reason == "false":
                return _Outcome("discard", steps=steps)
            if exc.clause == "ensures":
                return _Outcome(
                    "ensures",
                    steps=steps,
                    message=exc.message,
                    clause_index=exc.clause_index,
                    result=exc.result_display,
                )
            # A requires clause that used '?' and propagated: the
            # precondition could not be evaluated, which is a panic.
        failure = classify_call_failure(exc)
        if failure is None:
            raise exc
        if failure.kind == "limit":
            if isinstance(exc, StepLimitExceeded) and not full:
                # The call began with less than a full allowance because the
                # phase budget was nearly spent; running out says nothing.
                return _Outcome("budget", steps=steps)
            message = failure.message
            if isinstance(exc, SandboxTimeout):
                message = (
                    f"Execution timed out after {self.config.call_seconds:g} "
                    "seconds (the property call deadline)"
                )
            return _Outcome("limit", steps=steps, message=message, limit=failure.limit)
        if failure.kind == "refused":
            return _Outcome(
                "refused",
                steps=steps,
                message=f"needs a capability at run time: {failure.message}",
            )
        if target.has_panics_example:
            # A panics example says the function panics on purpose.
            return _Outcome("pass", steps=steps)
        return _Outcome("panic", steps=steps, message=failure.message)

    def failure(self, target: PropertyTarget, outcome: _Outcome) -> PropertyFailure:
        """The reported failure for a failing outcome."""
        if outcome.kind == "ensures":
            clause = (
                ensures_clause_text(
                    self.source, target.definition, outcome.clause_index
                )
                if outcome.clause_index is not None
                else None
            )
            shown = f"ensures {clause}" if clause else "an ensures clause"
            result = (
                f" (result was {outcome.result})" if outcome.result is not None else ""
            )
            return PropertyFailure(
                "ensures",
                outcome.message,
                clause=clause,
                result=outcome.result,
                summary=f"{shown} failed{result}",
            )
        if outcome.kind == "limit":
            limit = outcome.limit or "resource limit"
            if limit == "step limit":
                detail = f" ({self.config.call_steps:,} steps per property call)"
            elif limit == "timeout":
                detail = f" ({self.config.call_seconds:g} s per property call)"
            else:
                detail = ""
            return PropertyFailure(
                "limit",
                outcome.message,
                limit=limit,
                summary=f"hit the {limit}{detail}",
            )
        return PropertyFailure(
            "panic", outcome.message, summary=f"panicked: {outcome.message}"
        )


def check_module_properties(
    program: Program,
    checker: TypeChecker,
    *,
    module: str,
    path: str,
    config: PropertyConfig | None = None,
    sandbox_config: SandboxConfig | None = None,
    modules: Mapping[str, Program] | None = None,
    source: str | None = None,
) -> list[PropertyResult]:
    """Property-check every eligible function of a type-checked module.

    *checker* is the checker that checked *program* (in project mode, that
    module's own checker). *module* is the logical module name the random
    streams are keyed by: the project module name, or the file stem for a
    single file; never a path, so a seed reproduces from any checkout.
    *path* is only displayed. *modules* are the parsed modules the program
    imports, as for ``Interpreter.run``. *source* is the module's text, used
    to quote a failing ``ensures`` clause.

    The module is loaded in an interpreter of its own with no capabilities,
    so property work never shares the step counter or output of the
    interpreter that ran the examples.
    """
    from .interpreter import Interpreter
    from .test_runner import default_test_sandbox_config

    config = config or PropertyConfig()
    interp = Interpreter(
        check_examples=False,
        sandbox_config=sandbox_config or default_test_sandbox_config(),
        capabilities=frozenset(),
    )
    try:
        interp.run(
            program, modules=dict(modules) if modules else None, execute_main=False
        )
    except CALL_FAILURES as exc:
        message = getattr(exc, "message", None) or str(exc)
        generators = Generators(checker.type_defs)
        return [
            PropertyResult(
                path=path,
                function=target.name,
                line=(
                    target.definition.location.line
                    if target.definition.location is not None
                    else None
                ),
                status="skipped",
                note=f"the module could not be loaded for property checks: {message}",
            )
            for target in property_targets(
                program, checker, generators, filter_pattern=config.filter_pattern
            )
        ]
    runner = PropertyRunner(
        interp, checker, module=module, path=path, config=config, source=source
    )
    return runner.check(program)
