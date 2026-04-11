"""Enrollment registry — which (username, serial) pairs are trusted keys.

Separate from ``enroll.py`` so tests and the handler can import the
registry without pulling in the interactive CLI.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from usbypass.config import ENROLLED_PATH, VAR_DIR


def _ensure_var_dir() -> None:
    VAR_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(VAR_DIR, 0o755)
    except PermissionError:
        pass


def load_registry(path: Path = ENROLLED_PATH) -> dict[str, list[dict[str, Any]]]:
    """Return ``{username: [entry, ...]}``. Missing file -> empty dict."""
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Normalize legacy shapes defensively.
    out: dict[str, list[dict[str, Any]]] = {}
    for user, entries in data.items():
        if isinstance(entries, list):
            out[user] = [e for e in entries if isinstance(e, dict)]
    return out


def save_registry(registry: dict[str, list[dict[str, Any]]], path: Path = ENROLLED_PATH) -> None:
    _ensure_var_dir()
    fd, tmp = tempfile.mkstemp(prefix=".enrolled.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(registry, f, indent=2, sort_keys=True)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def add_entry(username: str, serial: str, label: str | None, *, path: Path = ENROLLED_PATH) -> dict[str, Any]:
    registry = load_registry(path)
    entries = registry.setdefault(username, [])
    for entry in entries:
        if entry.get("serial") == serial:
            # Update label/timestamp in place — re-enrollment is allowed.
            entry["label"] = label or entry.get("label")
            entry["enrolled_at"] = time.time()
            save_registry(registry, path)
            return entry
    new_entry = {
        "serial": serial,
        "label": label or f"usb-{serial[:8]}",
        "enrolled_at": time.time(),
    }
    entries.append(new_entry)
    save_registry(registry, path)
    return new_entry


def remove_entry(username: str, serial_or_label: str, *, path: Path = ENROLLED_PATH) -> bool:
    registry = load_registry(path)
    entries = registry.get(username, [])
    before = len(entries)
    registry[username] = [
        e
        for e in entries
        if e.get("serial") != serial_or_label and e.get("label") != serial_or_label
    ]
    if not registry[username]:
        registry.pop(username, None)
    changed = len(registry.get(username, [])) != before
    if changed:
        save_registry(registry, path)
    return changed


def is_enrolled(username: str, serial: str, *, path: Path = ENROLLED_PATH) -> bool:
    for entry in load_registry(path).get(username, []):
        if entry.get("serial") == serial:
            return True
    return False
