"""Pytest fixtures — redirect all USBYPASS paths into a tmpdir.

Lets the test suite run as an unprivileged user without touching
`/etc`, `/var`, or `/run`. The env vars are consumed by ``config.py``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


@pytest.fixture(autouse=True)
def tmp_usbypass_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    etc = tmp_path / "etc"
    var = tmp_path / "var"
    run = tmp_path / "run"
    etc.mkdir(mode=0o700)
    var.mkdir()
    run.mkdir()
    monkeypatch.setenv("USBYPASS_ETC_DIR", str(etc))
    monkeypatch.setenv("USBYPASS_VAR_DIR", str(var))
    monkeypatch.setenv("USBYPASS_RUN_DIR", str(run))

    # Reload the config module so it picks up the env vars. Do this
    # per-test to keep isolation strict.
    import importlib

    import usbypass.config as cfg_mod

    importlib.reload(cfg_mod)
    # Modules that imported symbols from config must also be reloaded
    # so their module-level references point at the fresh paths.
    for name in (
        "usbypass.crypto",
        "usbypass.state",
        "usbypass.enrollment",
        "usbypass.usb",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
    yield
