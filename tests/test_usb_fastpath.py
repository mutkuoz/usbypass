"""Tests for usbypass.usb fast-path helpers (no pyudev required).

We don't plug real USB sticks into CI, so these tests exercise the
pure logic in ``_read_mountinfo``, ``_unescape_mountinfo``,
``read_handshake`` / ``write_handshake``, and ``iter_enrolled_matches``.
"""

from __future__ import annotations

from pathlib import Path

from usbypass import usb


def test_unescape_mountinfo() -> None:
    assert usb._unescape_mountinfo("a\\040b") == "a b"
    assert usb._unescape_mountinfo("a\\011b") == "a\tb"
    assert usb._unescape_mountinfo("a\\134b") == "a\\b"
    assert usb._unescape_mountinfo("plain") == "plain"


def test_read_handshake_missing_returns_none(tmp_path: Path) -> None:
    assert usb.read_handshake(tmp_path) is None


def test_write_and_read_handshake_round_trip(tmp_path: Path) -> None:
    payload = b"\x01" * 32
    written = usb.write_handshake(tmp_path, payload)
    assert written.exists()
    assert written.parent.name == ".usbypass"
    assert usb.read_handshake(tmp_path) == payload


def test_write_handshake_overwrites(tmp_path: Path) -> None:
    usb.write_handshake(tmp_path, b"A" * 32)
    usb.write_handshake(tmp_path, b"B" * 32)
    assert usb.read_handshake(tmp_path) == b"B" * 32


def test_iter_enrolled_matches() -> None:
    registry = {
        "alice": [{"serial": "S1", "label": "p"}, {"serial": "S2", "label": "b"}],
        "bob": [{"serial": "S3", "label": "x"}],
    }
    assert list(usb.iter_enrolled_matches("S1", registry)) == ["alice"]
    assert list(usb.iter_enrolled_matches("S3", registry)) == ["bob"]
    assert list(usb.iter_enrolled_matches("NONE", registry)) == []


def test_iter_enrolled_matches_no_duplicate_user() -> None:
    """If a user has the same serial listed twice, yield them once."""
    registry = {
        "alice": [
            {"serial": "S1", "label": "p"},
            {"serial": "S1", "label": "dup"},
        ],
    }
    assert list(usb.iter_enrolled_matches("S1", registry)) == ["alice"]
