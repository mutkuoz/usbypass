"""Verified-state file management.

``/run/usbypass/state.json`` is written by the udev handler when an
enrolled key is inserted and verified. The PAM helper only reads it.
Keeping this to pure stdlib avoids pulling pyudev into the PAM hot path.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from usbypass.config import RUN_DIR, STATE_FILE


def _ensure_run_dir() -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(RUN_DIR, 0o755)
    except PermissionError:
        pass


def write_state(username: str, serial: str, devnode: str | None = None) -> None:
    """Write the verified-state file atomically.

    Called by the udev handler after HMAC verification succeeds.
    Unverified state must *never* be written here.
    """
    _ensure_run_dir()
    payload: dict[str, Any] = {
        "username": username,
        "serial": serial,
        "devnode": devnode,
        "verified_at": time.time(),
    }
    # Atomic write via tmpfile + rename in the same directory.
    fd, tmp = tempfile.mkstemp(prefix=".state.", dir=str(RUN_DIR))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, STATE_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def clear_state() -> None:
    """Remove the state file. Idempotent."""
    try:
        STATE_FILE.unlink()
    except FileNotFoundError:
        pass


def read_state() -> dict[str, Any] | None:
    """Read the state file. Returns None if missing or unreadable.

    Called on the PAM hot path — must be fast and never raise.
    """
    try:
        with open(STATE_FILE, "rb") as f:
            return json.load(f)
    except (FileNotFoundError, PermissionError, ValueError, OSError):
        return None


def state_file_path() -> Path:
    return STATE_FILE
