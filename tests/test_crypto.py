"""Tests for usbypass.crypto."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from usbypass import crypto


def test_generate_and_load_secret() -> None:
    path = crypto.generate_secret()
    assert path.exists()
    st = os.stat(path)
    assert stat.S_IMODE(st.st_mode) == 0o600
    secret = crypto.load_secret()
    assert len(secret) == crypto.SECRET_BYTES


def test_generate_is_idempotent() -> None:
    crypto.generate_secret()
    first = crypto.load_secret()
    crypto.generate_secret()  # no force
    second = crypto.load_secret()
    assert first == second, "non-forced regenerate must not rewrite"


def test_generate_force_rewrites() -> None:
    crypto.generate_secret()
    first = crypto.load_secret()
    crypto.generate_secret(force=True)
    second = crypto.load_secret()
    assert first != second


def test_compute_handshake_is_deterministic() -> None:
    crypto.generate_secret()
    a = crypto.compute_handshake("alice", "ABC123")
    b = crypto.compute_handshake("alice", "ABC123")
    assert a == b
    assert len(a) == crypto.HANDSHAKE_BYTES


def test_handshake_differs_per_user() -> None:
    crypto.generate_secret()
    a = crypto.compute_handshake("alice", "ABC123")
    b = crypto.compute_handshake("bob", "ABC123")
    assert a != b


def test_handshake_differs_per_serial() -> None:
    crypto.generate_secret()
    a = crypto.compute_handshake("alice", "ABC123")
    b = crypto.compute_handshake("alice", "ABC124")
    assert a != b


def test_verify_positive() -> None:
    crypto.generate_secret()
    h = crypto.compute_handshake("alice", "ABC123")
    assert crypto.verify_handshake("alice", "ABC123", h) is True


def test_verify_negative_wrong_user() -> None:
    crypto.generate_secret()
    h = crypto.compute_handshake("alice", "ABC123")
    assert crypto.verify_handshake("bob", "ABC123", h) is False


def test_verify_negative_wrong_serial() -> None:
    crypto.generate_secret()
    h = crypto.compute_handshake("alice", "ABC123")
    assert crypto.verify_handshake("alice", "ABC124", h) is False


def test_verify_negative_tampered() -> None:
    crypto.generate_secret()
    h = bytearray(crypto.compute_handshake("alice", "ABC123"))
    h[0] ^= 0x01
    assert crypto.verify_handshake("alice", "ABC123", bytes(h)) is False


def test_verify_wrong_length_returns_false() -> None:
    crypto.generate_secret()
    assert crypto.verify_handshake("alice", "ABC123", b"\x00" * 10) is False
    assert crypto.verify_handshake("alice", "ABC123", b"") is False


def test_load_secret_rejects_loose_perms(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = crypto.generate_secret()
    os.chmod(path, 0o644)
    with pytest.raises(crypto.SecretMissingError):
        crypto.load_secret()


def test_load_secret_missing_raises() -> None:
    with pytest.raises(crypto.SecretMissingError):
        crypto.load_secret()


def test_compute_rejects_empty_inputs() -> None:
    crypto.generate_secret()
    with pytest.raises(ValueError):
        crypto.compute_handshake("", "abc")
    with pytest.raises(ValueError):
        crypto.compute_handshake("alice", "")
