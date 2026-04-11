"""Tests for usbypass.state — atomic state file management."""

from __future__ import annotations

import os
import stat

from usbypass import state


def test_read_state_missing() -> None:
    assert state.read_state() is None


def test_write_and_read_state() -> None:
    state.write_state(username="alice", serial="ABC123", devnode="/dev/sdb1")
    data = state.read_state()
    assert data is not None
    assert data["username"] == "alice"
    assert data["serial"] == "ABC123"
    assert data["devnode"] == "/dev/sdb1"
    assert isinstance(data["verified_at"], float)


def test_state_file_permissions() -> None:
    state.write_state("alice", "ABC123")
    st = os.stat(state.state_file_path())
    # State file is world-readable on purpose: see state.write_state.
    assert stat.S_IMODE(st.st_mode) == 0o644


def test_clear_state_removes_file() -> None:
    state.write_state("alice", "ABC123")
    assert state.state_file_path().exists()
    state.clear_state()
    assert not state.state_file_path().exists()


def test_clear_state_idempotent() -> None:
    state.clear_state()  # absent
    state.clear_state()  # still absent; must not raise


def test_read_state_handles_corrupted_file() -> None:
    state.state_file_path().parent.mkdir(parents=True, exist_ok=True)
    state.state_file_path().write_text("this is not json")
    assert state.read_state() is None
