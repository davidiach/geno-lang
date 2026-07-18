"""Tests for scripts/audit_release_lock.py."""

from scripts import audit_release_lock


def test_release_lock_audit_skips_non_linux(monkeypatch):
    monkeypatch.setattr(audit_release_lock.sys, "platform", "win32")

    assert audit_release_lock.build_command() is None


def test_release_lock_audit_is_hash_strict_on_linux(monkeypatch):
    monkeypatch.setattr(audit_release_lock.sys, "platform", "linux")
    command = audit_release_lock.build_command()

    assert command is not None
    assert command[1:] == (
        "-m",
        "pip_audit",
        "--require-hashes",
        "-r",
        "requirements-release.lock",
        "--strict",
        "--progress-spinner",
        "off",
    )
