"""Tests for the effect typing system — Phase 1: type representation and builtin effects."""

from geno.builtin_registry import (
    CAPABILITY_TO_EFFECT,
    VALID_EFFECTS,
    build_builtin_signatures,
    source_builtin_specs,
)
from geno.types import FuncType, IntType, StringType, UnitType

# ---------------------------------------------------------------------------
# FuncType.effects field
# ---------------------------------------------------------------------------


class TestFuncTypeEffects:
    def test_default_effects_empty(self):
        ft = FuncType((IntType(),), IntType())
        assert ft.effects == frozenset()

    def test_explicit_effects(self):
        ft = FuncType((IntType(),), IntType(), frozenset({"fs"}))
        assert ft.effects == frozenset({"fs"})

    def test_str_no_effects(self):
        ft = FuncType((IntType(),), IntType())
        assert str(ft) == "(Int) -> Int"

    def test_str_single_effect(self):
        ft = FuncType((StringType(),), StringType(), frozenset({"fs"}))
        assert str(ft) == "(String) -> String with fs"

    def test_str_multiple_effects_sorted(self):
        ft = FuncType((), UnitType(), frozenset({"io", "fs"}))
        assert str(ft) == "() -> Unit with fs, io"

    def test_equality_with_same_effects(self):
        a = FuncType((IntType(),), IntType(), frozenset({"fs"}))
        b = FuncType((IntType(),), IntType(), frozenset({"fs"}))
        assert a == b

    def test_inequality_different_effects(self):
        a = FuncType((IntType(),), IntType(), frozenset({"fs"}))
        b = FuncType((IntType(),), IntType(), frozenset({"http"}))
        assert a != b

    def test_inequality_effects_vs_no_effects(self):
        a = FuncType((IntType(),), IntType())
        b = FuncType((IntType(),), IntType(), frozenset({"fs"}))
        assert a != b

    def test_hashable_with_effects(self):
        a = FuncType((IntType(),), IntType(), frozenset({"fs"}))
        b = FuncType((IntType(),), IntType(), frozenset({"fs"}))
        assert hash(a) == hash(b)
        s = {a, b}
        assert len(s) == 1

    def test_frozen(self):
        ft = FuncType((IntType(),), IntType(), frozenset({"fs"}))
        try:
            ft.effects = frozenset()  # type: ignore[misc]
            raise AssertionError("Should not be able to assign to frozen dataclass")
        except AttributeError:
            pass


# ---------------------------------------------------------------------------
# Builtin effect population
# ---------------------------------------------------------------------------


class TestBuiltinEffects:
    def test_pure_builtin_has_no_effects(self):
        sigs = build_builtin_signatures()
        assert sigs["length"].effects == frozenset()

    def test_fs_builtin_has_fs_effect(self):
        sigs = build_builtin_signatures()
        assert sigs["fs_read_text"].effects == frozenset({"fs"})
        assert sigs["fs_write_text"].effects == frozenset({"fs"})

    def test_http_builtin_has_http_effect(self):
        sigs = build_builtin_signatures()
        assert sigs["http_fetch"].effects == frozenset({"http"})
        assert sigs["http_post"].effects == frozenset({"http"})

    def test_print_builtin_has_io_effect(self):
        sigs = build_builtin_signatures()
        assert sigs["print"].effects == frozenset({"io"})

    def test_clock_builtin_has_clock_effect(self):
        sigs = build_builtin_signatures()
        assert sigs["clock_now"].effects == frozenset({"clock"})

    def test_random_builtin_has_random_effect(self):
        sigs = build_builtin_signatures()
        assert sigs["random_int"].effects == frozenset({"random"})

    def test_process_builtin_has_process_effect(self):
        sigs = build_builtin_signatures()
        assert sigs["exec"].effects == frozenset({"process"})

    def test_env_builtin_has_env_effect(self):
        sigs = build_builtin_signatures()
        assert sigs["env_get"].effects == frozenset({"env"})
        assert sigs["cli_args"].effects == frozenset({"env"})

    def test_regex_builtin_has_regex_effect(self):
        sigs = build_builtin_signatures()
        assert sigs["regex_match"].effects == frozenset({"regex"})

    def test_serve_builtin_has_http_effect(self):
        sigs = build_builtin_signatures()
        assert sigs["http_listen"].effects == frozenset({"http"})

    def test_builtin_spec_signature_has_effects(self):
        """BuiltinSpec.signature should carry effects too."""
        specs = source_builtin_specs()
        assert specs["fs_read_text"].signature is not None
        assert specs["fs_read_text"].signature.effects == frozenset({"fs"})

    def test_all_gated_builtins_have_effects(self):
        """Every builtin with a capability should have a non-empty effect set."""
        specs = source_builtin_specs()
        for name, spec in specs.items():
            if spec.capability is not None and spec.signature is not None:
                assert spec.signature.effects, (
                    f"Gated builtin '{name}' (capability={spec.capability}) "
                    f"should have effects but has {spec.signature.effects}"
                )

    def test_all_pure_builtins_have_no_effects(self):
        """Every builtin without a capability should have empty effects."""
        specs = source_builtin_specs()
        for name, spec in specs.items():
            if spec.capability is None and spec.signature is not None:
                assert not spec.signature.effects, (
                    f"Pure builtin '{name}' should have no effects "
                    f"but has {spec.signature.effects}"
                )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestEffectConstants:
    def test_capability_to_effect_covers_all_capabilities(self):
        """Every capability string used in the manifest should map to an effect."""
        specs = source_builtin_specs()
        capabilities_in_use = {
            spec.capability for spec in specs.values() if spec.capability is not None
        }
        for cap in capabilities_in_use:
            assert cap in CAPABILITY_TO_EFFECT, (
                f"Capability '{cap}' is used in builtins but not in CAPABILITY_TO_EFFECT"
            )

    def test_all_effect_values_are_valid(self):
        """Every effect produced by CAPABILITY_TO_EFFECT should be in VALID_EFFECTS."""
        for cap, effect in CAPABILITY_TO_EFFECT.items():
            assert effect in VALID_EFFECTS, (
                f"CAPABILITY_TO_EFFECT['{cap}'] = '{effect}' is not in VALID_EFFECTS"
            )
