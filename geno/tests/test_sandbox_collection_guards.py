"""
Sandbox collection-limit guard tests — exercise security-critical paths that
enforce ``SandboxConfig.max_collection_size`` on stdlib functions whose output
size is caller-controlled.

These defense-in-depth guards (``geno/sandbox.py`` lines ~595-719) prevent
memory-exhaustion DoS from sandboxed (LLM-generated) code that requests huge
outputs from ``secrets`` token helpers or ``hashlib`` SHAKE extendable-output
functions. They are reached through the module proxy returned by
``_create_module_proxy`` / the sandbox ``__import__`` shim.

Covered branches:
  * ``secrets.token_bytes`` / ``token_hex`` / ``token_urlsafe`` size guards
  * ``hashlib.shake_128`` / ``shake_256`` ``digest()`` / ``hexdigest()`` guards
  * ``hashlib.new("shake_*")`` routed through the SHAKE proxy
  * SHAKE proxy ``copy()`` re-wrapping, dunder/private attribute blocking, repr
"""

import hashlib
import secrets

import pytest

from geno.sandbox import (
    SecurityViolation,
    _create_module_proxy,
)

LIMIT = 1000


@pytest.fixture
def secrets_proxy():
    return _create_module_proxy(secrets, max_collection_size=LIMIT)


@pytest.fixture
def hashlib_proxy():
    return _create_module_proxy(hashlib, max_collection_size=LIMIT)


# ---------------------------------------------------------------------------
# secrets.* token helpers  (lines 654-698)
# ---------------------------------------------------------------------------


class TestSecretsTokenGuards:
    """secrets token helpers must reject outputs above the collection limit."""

    def test_token_bytes_over_limit_raises(self, secrets_proxy):
        with pytest.raises(SecurityViolation, match="token_bytes output exceeds"):
            secrets_proxy.token_bytes(LIMIT + 1)

    def test_token_bytes_under_limit_returns_bytes(self, secrets_proxy):
        result = secrets_proxy.token_bytes(8)
        assert isinstance(result, bytes)
        assert len(result) == 8

    def test_token_bytes_default_size_allowed(self, secrets_proxy):
        """No-argument call uses the 32-byte default and is well under limit."""
        assert isinstance(secrets_proxy.token_bytes(), bytes)

    def test_token_hex_over_limit_raises(self, secrets_proxy):
        # hex output is 2 chars per byte, so LIMIT bytes -> 2*LIMIT chars.
        with pytest.raises(SecurityViolation, match="token_hex output exceeds"):
            secrets_proxy.token_hex(LIMIT)

    def test_token_hex_under_limit_returns_str(self, secrets_proxy):
        result = secrets_proxy.token_hex(8)
        assert isinstance(result, str)
        assert len(result) == 16

    def test_token_urlsafe_over_limit_raises(self, secrets_proxy):
        # urlsafe output is base64 (~4/3 expansion), so LIMIT bytes overflows.
        with pytest.raises(SecurityViolation, match="token_urlsafe output exceeds"):
            secrets_proxy.token_urlsafe(LIMIT)

    def test_token_urlsafe_under_limit_returns_str(self, secrets_proxy):
        assert isinstance(secrets_proxy.token_urlsafe(8), str)

    def test_token_nbytes_none_uses_default(self, secrets_proxy):
        """Explicit nbytes=None falls back to the default size (not a crash)."""
        assert isinstance(secrets_proxy.token_bytes(None), bytes)
        assert isinstance(secrets_proxy.token_hex(None), str)
        assert isinstance(secrets_proxy.token_urlsafe(None), str)

    def test_non_token_callable_passes_through(self, secrets_proxy):
        """secrets functions without a size argument are returned unwrapped."""
        assert callable(secrets_proxy.choice)


# ---------------------------------------------------------------------------
# hashlib SHAKE extendable-output functions  (lines 597-651, 699-718)
# ---------------------------------------------------------------------------


class TestShakeDigestGuards:
    """SHAKE digest()/hexdigest() length is caller-controlled and must be capped."""

    @pytest.mark.parametrize("factory", ["shake_128", "shake_256"])
    def test_digest_over_limit_raises(self, hashlib_proxy, factory):
        shake = getattr(hashlib_proxy, factory)(b"seed")
        with pytest.raises(SecurityViolation, match="shake digest output exceeds"):
            shake.digest(LIMIT + 1)

    @pytest.mark.parametrize("factory", ["shake_128", "shake_256"])
    def test_digest_under_limit_returns_bytes(self, hashlib_proxy, factory):
        shake = getattr(hashlib_proxy, factory)(b"seed")
        out = shake.digest(8)
        assert isinstance(out, bytes)
        assert len(out) == 8

    def test_hexdigest_over_limit_raises(self, hashlib_proxy):
        # hex doubles the size, so LIMIT bytes -> 2*LIMIT chars overflows.
        shake = hashlib_proxy.shake_128(b"seed")
        with pytest.raises(SecurityViolation, match="shake hex output exceeds"):
            shake.hexdigest(LIMIT)

    def test_hexdigest_under_limit_returns_str(self, hashlib_proxy):
        out = hashlib_proxy.shake_256(b"seed").hexdigest(8)
        assert isinstance(out, str)
        assert len(out) == 16

    def test_copy_rewraps_and_still_guards(self, hashlib_proxy):
        """copy() must return a guarded proxy, not the raw object."""
        clone = hashlib_proxy.shake_256(b"seed").copy()
        # The clone is still guarded.
        with pytest.raises(SecurityViolation, match="shake digest output exceeds"):
            clone.digest(LIMIT + 1)
        # And it behaves like a copy of the original.
        assert clone.digest(8) == hashlib_proxy.shake_256(b"seed").digest(8)

    def test_new_shake_routed_through_guard(self, hashlib_proxy):
        """hashlib.new('shake_256') must also be wrapped and guarded."""
        shake = hashlib_proxy.new("shake_256", b"seed")
        with pytest.raises(SecurityViolation, match="shake digest output exceeds"):
            shake.digest(LIMIT + 1)
        assert isinstance(shake.digest(8), bytes)

    def test_new_non_shake_not_wrapped(self, hashlib_proxy):
        """Fixed-length algorithms have no length argument and pass through."""
        digest = hashlib_proxy.new("sha256", b"seed").hexdigest()
        assert digest == hashlib.sha256(b"seed").hexdigest()

    def test_fixed_length_factory_passes_through(self, hashlib_proxy):
        """Non-SHAKE hash factories are returned unwrapped."""
        assert hashlib_proxy.sha256(b"seed").hexdigest() == (
            hashlib.sha256(b"seed").hexdigest()
        )


# ---------------------------------------------------------------------------
# SHAKE proxy attribute filtering  (lines 601-614, 648-649)
# ---------------------------------------------------------------------------


class TestShakeProxyAttributeFiltering:
    """The SHAKE proxy enforces the same attribute policy as the module proxy."""

    def test_blocks_unsafe_dunder(self, hashlib_proxy):
        shake = hashlib_proxy.shake_256(b"seed")
        with pytest.raises(SecurityViolation, match="__class__"):
            _ = shake.__class__

    def test_blocks_non_safe_dunder(self, hashlib_proxy):
        """A dunder that is neither blocklisted nor in SAFE_DUNDERS is rejected."""
        shake = hashlib_proxy.shake_256(b"seed")
        with pytest.raises(SecurityViolation, match="__sizeof__"):
            _ = shake.__sizeof__

    def test_blocks_private_attribute(self, hashlib_proxy):
        shake = hashlib_proxy.shake_256(b"seed")
        with pytest.raises(SecurityViolation, match="private attribute"):
            _ = shake._not_allowed

    def test_allows_safe_non_guarded_attribute(self, hashlib_proxy):
        """Ordinary attributes (digest_size) pass through the SHAKE proxy."""
        assert isinstance(hashlib_proxy.shake_256(b"seed").digest_size, int)

    def test_repr_does_not_leak_real_object(self, hashlib_proxy):
        shake = hashlib_proxy.shake_256(b"seed")
        assert repr(shake) == "<sandboxed hashlib shake object>"
