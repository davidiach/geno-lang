"""Large-program effect stabilization, imported only after the size threshold."""

from __future__ import annotations

from array import array
from bisect import bisect_left
from collections import deque
from typing import TYPE_CHECKING

from .ast_nodes import FunctionDef, ImplDef
from .types import FuncType, Type, TypeEnv

if TYPE_CHECKING:
    from .typechecker import TypeChecker

_ImplEffectItem = tuple[ImplDef, FunctionDef]
_SPARSE_CHANGE_DIVISOR = 8


def _impl_effect_item_name(item: _ImplEffectItem) -> str:
    name: str = item[1].name
    return name


class _EffectTrackingRoot(TypeEnv):
    """Record changeable global providers after ordinary local shadowing."""

    def __init__(
        self,
        parent: TypeEnv,
        provider_names: list[str],
        observed_providers: set[int],
    ) -> None:
        super().__init__(parent=parent)
        self._provider_names = provider_names
        self._observed_providers = observed_providers

    def lookup(self, name: str) -> Type | None:
        if name in self.bindings:
            return self.bindings[name]
        provider = bisect_left(self._provider_names, name)
        if (
            provider < len(self._provider_names)
            and self._provider_names[provider] == name
        ):
            self._observed_providers.add(provider)
        if self.parent is None:
            return None
        return self.parent.lookup(name)


def stabilize_function_effects(
    checker: TypeChecker,
    functions: list[FunctionDef],
    impl_methods: list[_ImplEffectItem],
) -> None:
    """Continue dense legacy waves, then switch a sparse wave to the graph."""
    initial_fresh_tv_counter = checker._fresh_tv_counter
    initial_error_prefix = len(checker.errors)
    item_count = len(functions) + len(impl_methods)
    while True:
        changed = False
        changed_count = 0

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
                changed_count += 1

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
                changed_count += 1

        if not changed:
            return
        if len(checker.errors) != initial_error_prefix:
            continue
        if changed_count * _SPARSE_CHANGE_DIVISOR > item_count:
            continue
        return _EffectSparseScheduler(
            checker,
            functions,
            impl_methods,
            initial_fresh_tv_counter=initial_fresh_tv_counter,
            initial_error_prefix=initial_error_prefix,
        ).run()


def _finish_legacy(
    checker: TypeChecker,
    functions: list[FunctionDef],
    impl_methods: list[_ImplEffectItem],
) -> None:
    """Finish with the original sweep after a sparse diagnostic rollback."""
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


class _EffectSparseScheduler:
    """Lazy dependency scheduler for large, sparsely changing effect graphs."""

    __slots__ = (
        "checker",
        "current_return_type",
        "edge_consumer",
        "edge_next",
        "error_prefix",
        "function_count",
        "functions",
        "impl_methods",
        "impl_provider_items",
        "in_async_function",
        "in_main_function",
        "in_queue",
        "initial_error_prefix",
        "initial_fresh_tv_counter",
        "item_count",
        "lambda_return_types",
        "lambda_return_values",
        "loop_depth",
        "observed_providers",
        "queue",
        "resolving_alias_values",
        "resolving_aliases",
        "reverse_head",
        "top_provider_names",
        "tracking_root",
    )

    def __init__(
        self,
        checker: TypeChecker,
        functions: list[FunctionDef],
        impl_methods: list[_ImplEffectItem],
        *,
        initial_fresh_tv_counter: int,
        initial_error_prefix: int,
    ) -> None:
        self.checker = checker
        self.functions = functions
        self.impl_methods = impl_methods
        self.function_count = len(functions)
        self.item_count = self.function_count + len(impl_methods)
        self.initial_fresh_tv_counter = initial_fresh_tv_counter
        self.initial_error_prefix = initial_error_prefix
        self.error_prefix = len(checker.errors)
        self.current_return_type = checker.current_return_type
        self.lambda_return_types = checker._lambda_return_types
        self.lambda_return_values = (
            None if self.lambda_return_types is None else list(self.lambda_return_types)
        )
        self.in_async_function = checker._in_async_function
        self.in_main_function = checker._in_main_function
        self.loop_depth = checker._loop_depth
        self.resolving_aliases = checker._resolving_aliases
        self.resolving_alias_values = tuple(self.resolving_aliases)

        self.top_provider_names = [
            defn.name
            for ordinal, defn in enumerate(functions)
            if not defn.effects and isinstance(self._type_at(ordinal), FuncType)
        ]
        self.top_provider_names.sort()
        self.impl_provider_items = [
            item
            for offset, item in enumerate(impl_methods)
            if not item[1].effects
            and isinstance(self._type_at(self.function_count + offset), FuncType)
        ]
        self.impl_provider_items.sort(key=_impl_effect_item_name)
        provider_count = len(self.top_provider_names) + len(self.impl_provider_items)

        self.reverse_head = array("i", [-1]) * provider_count
        self.edge_consumer = array("I")
        self.edge_next = array("i")
        self.in_queue = bytearray(self.item_count)
        self.queue: deque[int] = deque()
        self.observed_providers: set[int] = set()
        self.tracking_root = _EffectTrackingRoot(
            checker.global_env,
            self.top_provider_names,
            self.observed_providers,
        )

    def observe_trait_slot(
        self,
        method_name: str,
        trait_name: str,
        resolved_type_name: str,
    ) -> None:
        """Record one resolved impl slot during tracked discovery."""
        provider = bisect_left(
            self.impl_provider_items,
            method_name,
            key=_impl_effect_item_name,
        )
        while (
            provider < len(self.impl_provider_items)
            and self.impl_provider_items[provider][1].name == method_name
        ):
            provider_impl, _provider_method = self.impl_provider_items[provider]
            if (
                provider_impl.trait_name == trait_name
                and provider_impl.target_type == resolved_type_name
            ):
                self.observed_providers.add(len(self.top_provider_names) + provider)
                return
            provider += 1

    def _defn_at(self, ordinal: int) -> FunctionDef:
        if ordinal < self.function_count:
            return self.functions[ordinal]
        return self.impl_methods[ordinal - self.function_count][1]

    def _type_at(self, ordinal: int) -> Type | None:
        if ordinal < self.function_count:
            return self.checker.global_env.lookup(self.functions[ordinal].name)
        impl_def, method = self.impl_methods[ordinal - self.function_count]
        method_types = self.checker.impl_registry.get(
            (impl_def.trait_name, impl_def.target_type)
        )
        if method_types is None:
            return None
        return method_types.get(method.name)

    def _set_type(self, ordinal: int, function_type: FuncType) -> None:
        if ordinal < self.function_count:
            self.checker.global_env.bind(
                self.functions[ordinal].name,
                function_type,
            )
            return
        impl_def, method = self.impl_methods[ordinal - self.function_count]
        method_types = self.checker.impl_registry.get(
            (impl_def.trait_name, impl_def.target_type)
        )
        if method_types is not None and method.name in method_types:
            method_types[method.name] = function_type

    def _provider_for_ordinal(self, ordinal: int) -> int | None:
        if ordinal < self.function_count:
            name = self.functions[ordinal].name
            provider = bisect_left(self.top_provider_names, name)
            if (
                provider < len(self.top_provider_names)
                and self.top_provider_names[provider] == name
            ):
                return provider
            return None

        impl_def, method = self.impl_methods[ordinal - self.function_count]
        provider = bisect_left(
            self.impl_provider_items,
            method.name,
            key=_impl_effect_item_name,
        )
        while (
            provider < len(self.impl_provider_items)
            and self.impl_provider_items[provider][1].name == method.name
        ):
            provider_impl, provider_method = self.impl_provider_items[provider]
            if provider_impl is impl_def and provider_method is method:
                return len(self.top_provider_names) + provider
            provider += 1
        return None

    def _tracked_effects(self, defn: FunctionDef) -> frozenset[str]:
        local_env = self.tracking_root.child()
        for param in defn.params:
            local_env.bind(
                param.name,
                self.checker._resolve_type(param.param_type),
            )
        inferred = self.checker._infer_function_effects(defn, local_env)
        if defn.effects:
            return frozenset(defn.effects)
        return inferred

    def _enqueue_consumers(self, provider: int) -> None:
        edge = self.reverse_head[provider]
        while edge != -1:
            consumer = self.edge_consumer[edge]
            if not self.in_queue[consumer]:
                self.in_queue[consumer] = 1
                self.queue.append(consumer)
            edge = self.edge_next[edge]

    def _evaluate(self, ordinal: int, *, track: bool) -> None:
        existing_type = self._type_at(ordinal)
        if not isinstance(existing_type, FuncType):
            return
        final_effects = (
            self._tracked_effects(self._defn_at(ordinal))
            if track
            else self.checker._stable_effects_for_function(self._defn_at(ordinal))
        )
        if existing_type.effects == final_effects:
            return
        self._set_type(
            ordinal,
            FuncType(
                existing_type.param_types,
                existing_type.return_type,
                final_effects,
            ),
        )
        provider = self._provider_for_ordinal(ordinal)
        if provider is not None:
            self._enqueue_consumers(provider)

    def _reset_changeable_signatures(self) -> None:
        for ordinal in range(self.item_count):
            defn = self._defn_at(ordinal)
            existing_type = self._type_at(ordinal)
            if (
                defn.effects
                or not isinstance(existing_type, FuncType)
                or not existing_type.effects
            ):
                continue
            self._set_type(
                ordinal,
                FuncType(
                    existing_type.param_types,
                    existing_type.return_type,
                    frozenset(),
                ),
            )

    def _fallback_to_legacy(self) -> None:
        self._reset_changeable_signatures()
        del self.checker.errors[self.initial_error_prefix :]
        self.checker._fresh_tv_counter = self.initial_fresh_tv_counter
        self.checker._resolved_type_cache.clear()
        self.checker.current_return_type = self.current_return_type
        if self.lambda_return_types is None:
            self.checker._lambda_return_types = None
        else:
            self.lambda_return_types[:] = self.lambda_return_values or []
            self.checker._lambda_return_types = self.lambda_return_types
        self.checker._in_async_function = self.in_async_function
        self.checker._in_main_function = self.in_main_function
        self.checker._loop_depth = self.loop_depth
        self.resolving_aliases.clear()
        self.resolving_aliases.update(self.resolving_alias_values)
        self.checker._resolving_aliases = self.resolving_aliases
        self.checker._effect_trait_observer = None
        _finish_legacy(self.checker, self.functions, self.impl_methods)

    def _has_new_errors(self) -> bool:
        return len(self.checker.errors) != self.error_prefix

    def run(self) -> None:
        self.checker._effect_trait_observer = self.observe_trait_slot
        try:
            for ordinal in range(self.item_count):
                defn = self._defn_at(ordinal)
                if defn.effects or not isinstance(self._type_at(ordinal), FuncType):
                    continue
                self.observed_providers.clear()
                self._evaluate(ordinal, track=True)
                if self._has_new_errors():
                    self._fallback_to_legacy()
                    return
                for provider in self.observed_providers:
                    self.edge_consumer.append(ordinal)
                    self.edge_next.append(self.reverse_head[provider])
                    self.reverse_head[provider] = len(self.edge_consumer) - 1

            self.checker._effect_trait_observer = None
            while self.queue:
                ordinal = self.queue.popleft()
                self.in_queue[ordinal] = 0
                self._evaluate(ordinal, track=False)
                if self._has_new_errors():
                    self._fallback_to_legacy()
                    return

            # Declared signatures are fixed, but their bodies can reveal a
            # delayed higher-order diagnostic after changeable callees settle.
            for ordinal in range(self.item_count):
                defn = self._defn_at(ordinal)
                if not defn.effects or not isinstance(self._type_at(ordinal), FuncType):
                    continue
                self.checker._stable_effects_for_function(defn)
                if self._has_new_errors():
                    self._fallback_to_legacy()
                    return
        finally:
            del self.checker._effect_trait_observer
