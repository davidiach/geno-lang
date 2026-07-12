"""Tests for the effect typing system — Phase 4: sandbox integration."""

from geno.diagnostics import ErrorCode


class TestEffectErrorCodes:
    def test_effect_violation_code_exists(self):
        assert ErrorCode.EFFECT_VIOLATION.value == "E310"

    def test_effect_unknown_code_exists(self):
        assert ErrorCode.EFFECT_UNKNOWN.value == "E311"

    def test_effect_mismatch_code_exists(self):
        assert ErrorCode.EFFECT_MISMATCH.value == "E312"
