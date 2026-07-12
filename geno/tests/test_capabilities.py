"""Tests for shared capability parsing."""

from __future__ import annotations

import pytest

from geno.api import RunConfig
from geno.capabilities import CapabilityParseError, normalize_capability_values


def test_normalize_capability_values_splits_cli_commas():
    assert normalize_capability_values(["fs, print", "clock"], allow_comma=True) == {
        "clock",
        "fs",
        "print",
    }


def test_normalize_capability_values_rejects_unknown_with_suggestion():
    with pytest.raises(CapabilityParseError, match="Did you mean 'fs'"):
        normalize_capability_values(["fss"])


def test_normalize_capability_values_rejects_empty_items():
    with pytest.raises(CapabilityParseError, match="must not be empty"):
        normalize_capability_values(["print,,"], allow_comma=True)


def test_run_config_rejects_unknown_capability():
    with pytest.raises(ValueError, match="Unknown capability 'fss'"):
        RunConfig(capabilities={"fss"})


def test_known_capabilities_match_registry_capability_map():
    """The light manifest-derived name set must equal the registry's map keys.

    geno.capabilities deliberately avoids importing geno.builtin_registry
    (CLI startup cost); this pins that the two derivations of the
    capability-name universe can never drift apart.
    """
    from geno.builtin_registry import CAPABILITY_MAP, DEFAULT_ALLOWED_CAPABILITIES
    from geno.capabilities import (
        DEFAULT_ALLOWED_CAPABILITIES as LIGHT_DEFAULTS,
    )
    from geno.capabilities import (
        KNOWN_CAPABILITIES,
    )

    assert frozenset(CAPABILITY_MAP) == KNOWN_CAPABILITIES
    assert DEFAULT_ALLOWED_CAPABILITIES is LIGHT_DEFAULTS
    assert LIGHT_DEFAULTS <= KNOWN_CAPABILITIES
