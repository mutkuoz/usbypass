"""Tests for the enrollment registry (usbypass.enrollment).

These exercise the pure-Python layer that tracks (user, serial) trust
relationships. The interactive enroll.py CLI path is tested separately.
"""

from __future__ import annotations

from usbypass import enrollment


def test_load_registry_missing_returns_empty() -> None:
    assert enrollment.load_registry() == {}


def test_add_entry_creates_record() -> None:
    entry = enrollment.add_entry("alice", "SERIAL1", "primary")
    assert entry["serial"] == "SERIAL1"
    assert entry["label"] == "primary"
    assert "enrolled_at" in entry

    reg = enrollment.load_registry()
    assert "alice" in reg
    assert len(reg["alice"]) == 1
    assert reg["alice"][0]["serial"] == "SERIAL1"


def test_add_entry_default_label() -> None:
    entry = enrollment.add_entry("alice", "0123456789ABCDEF", None)
    assert entry["label"].startswith("usb-")


def test_add_entry_reenroll_updates_in_place() -> None:
    enrollment.add_entry("alice", "SERIAL1", "old")
    enrollment.add_entry("alice", "SERIAL1", "new")
    reg = enrollment.load_registry()
    assert len(reg["alice"]) == 1
    assert reg["alice"][0]["label"] == "new"


def test_add_multiple_keys_per_user() -> None:
    enrollment.add_entry("alice", "SERIAL1", "primary")
    enrollment.add_entry("alice", "SERIAL2", "backup")
    reg = enrollment.load_registry()
    serials = {e["serial"] for e in reg["alice"]}
    assert serials == {"SERIAL1", "SERIAL2"}


def test_is_enrolled() -> None:
    enrollment.add_entry("alice", "SERIAL1", "primary")
    assert enrollment.is_enrolled("alice", "SERIAL1")
    assert not enrollment.is_enrolled("alice", "SERIAL2")
    assert not enrollment.is_enrolled("bob", "SERIAL1")


def test_remove_entry_by_serial() -> None:
    enrollment.add_entry("alice", "SERIAL1", "primary")
    assert enrollment.remove_entry("alice", "SERIAL1") is True
    assert not enrollment.is_enrolled("alice", "SERIAL1")


def test_remove_entry_by_label() -> None:
    enrollment.add_entry("alice", "SERIAL1", "primary")
    assert enrollment.remove_entry("alice", "primary") is True
    assert enrollment.load_registry() == {}


def test_remove_nonexistent_returns_false() -> None:
    assert enrollment.remove_entry("alice", "NOPE") is False


def test_remove_last_key_drops_user_bucket() -> None:
    enrollment.add_entry("alice", "SERIAL1", "primary")
    enrollment.remove_entry("alice", "SERIAL1")
    assert "alice" not in enrollment.load_registry()


def test_load_registry_ignores_corrupted_file() -> None:
    enrollment.ENROLLED_PATH.parent.mkdir(parents=True, exist_ok=True)
    enrollment.ENROLLED_PATH.write_text("not json")
    assert enrollment.load_registry() == {}
