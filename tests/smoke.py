"""pytest-free smoke tests.

Replicates the critical assertions from the pytest suite so the
``make smoke`` target works on minimal build hosts that don't have
pytest installed (e.g. inside CI containers, package builders, etc.).

Run with::

    PYTHONPATH=src python3 tests/smoke.py
"""

from __future__ import annotations

import importlib
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

# Allow running from the project root with bare `python3 tests/smoke.py`.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _isolate() -> str:
    tmp = tempfile.mkdtemp(prefix="usbypass-smoke-")
    os.makedirs(f"{tmp}/etc", mode=0o700, exist_ok=True)
    os.makedirs(f"{tmp}/var", exist_ok=True)
    os.makedirs(f"{tmp}/run", exist_ok=True)
    os.environ["USBYPASS_ETC_DIR"] = f"{tmp}/etc"
    os.environ["USBYPASS_VAR_DIR"] = f"{tmp}/var"
    os.environ["USBYPASS_RUN_DIR"] = f"{tmp}/run"
    for name in (
        "usbypass.config",
        "usbypass.crypto",
        "usbypass.state",
        "usbypass.enrollment",
        "usbypass.usb",
    ):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
        else:
            importlib.import_module(name)
    return tmp


def test_imports() -> None:
    for name in (
        "usbypass",
        "usbypass.config",
        "usbypass.crypto",
        "usbypass.state",
        "usbypass.enrollment",
        "usbypass.usb",
        "usbypass.handler",
        "usbypass.verify",
        "usbypass.enroll",
        "usbypass.cli",
        "usbypass.interactive",
        "usbypass.ui",
        "usbypass.pam_installer",
    ):
        importlib.import_module(name)
    print("  ok  imports")


def test_crypto() -> None:
    tmp = _isolate()
    try:
        import usbypass.crypto as crypto
        path = crypto.generate_secret()
        assert path.exists()
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        s = crypto.load_secret()
        assert len(s) == 64
        first = crypto.load_secret()
        crypto.generate_secret()
        assert crypto.load_secret() == first
        crypto.generate_secret(force=True)
        assert crypto.load_secret() != first
        h = crypto.compute_handshake("alice", "ABC123")
        assert crypto.verify_handshake("alice", "ABC123", h)
        assert not crypto.verify_handshake("bob", "ABC123", h)
        assert not crypto.verify_handshake("alice", "ABC124", h)
        bad = bytearray(h)
        bad[0] ^= 0x01
        assert not crypto.verify_handshake("alice", "ABC123", bytes(bad))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("  ok  crypto")


def test_state() -> None:
    tmp = _isolate()
    try:
        import usbypass.state as state
        assert state.read_state() is None
        state.write_state(username="alice", serial="ABC123", devnode="/dev/sdb1")
        data = state.read_state()
        assert data and data["username"] == "alice"
        st = os.stat(state.state_file_path())
        assert stat.S_IMODE(st.st_mode) == 0o644
        state.clear_state()
        assert not state.state_file_path().exists()
        state.clear_state()  # idempotent
        # corrupted file → None
        state.state_file_path().parent.mkdir(parents=True, exist_ok=True)
        state.state_file_path().write_text("garbage")
        assert state.read_state() is None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("  ok  state")


def test_enrollment() -> None:
    tmp = _isolate()
    try:
        import usbypass.enrollment as enrollment
        assert enrollment.load_registry() == {}
        e1 = enrollment.add_entry("alice", "S1", "primary")
        assert e1["serial"] == "S1"
        e2 = enrollment.add_entry("alice", "S1", "renamed")
        assert e2["label"] == "renamed"
        reg = enrollment.load_registry()
        assert len(reg["alice"]) == 1
        enrollment.add_entry("alice", "S2", "backup")
        assert enrollment.is_enrolled("alice", "S1")
        assert enrollment.remove_entry("alice", "S1")
        assert not enrollment.remove_entry("alice", "NOPE")
        enrollment.remove_entry("alice", "backup")
        assert enrollment.load_registry() == {}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("  ok  enrollment")


def test_usb_helpers() -> None:
    import usbypass.usb as usb
    assert usb._unescape_mountinfo("a\\040b") == "a b"
    assert usb._unescape_mountinfo("a\\011b") == "a\tb"
    assert usb._unescape_mountinfo("a\\134b") == "a\\b"
    tmp = tempfile.mkdtemp()
    try:
        payload = b"\x01" * 32
        written = usb.write_handshake(Path(tmp), payload)
        assert written.exists()
        assert written.parent.name == ".usbypass"
        assert usb.read_handshake(Path(tmp)) == payload
        usb.write_handshake(Path(tmp), b"B" * 32)
        assert usb.read_handshake(Path(tmp)) == b"B" * 32
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    reg = {
        "alice": [{"serial": "S1", "label": "p"}, {"serial": "S2", "label": "b"}],
        "bob": [{"serial": "S3", "label": "x"}],
    }
    assert list(usb.iter_enrolled_matches("S1", reg)) == ["alice"]
    assert list(usb.iter_enrolled_matches("S3", reg)) == ["bob"]
    assert list(usb.iter_enrolled_matches("NONE", reg)) == []
    print("  ok  usb helpers")


def test_ui_formatters() -> None:
    from usbypass import ui
    assert ui.fmt_bytes(0) == "0 B"
    assert ui.fmt_bytes(1024).endswith("KB")
    assert ui.fmt_bytes(15_800_000_000).endswith("GB")
    assert ui.fmt_bytes(None) == "?"
    import time
    assert ui.fmt_relative(time.time() - 5) == "just now"
    assert "minute" in ui.fmt_relative(time.time() - 120)
    assert "hour" in ui.fmt_relative(time.time() - 7200)
    assert "day" in ui.fmt_relative(time.time() - 86400 * 3)
    print("  ok  ui formatters")


def test_pam_installer_marker() -> None:
    """Marker-based PAM edit must be a clean round-trip."""
    from usbypass import pam_installer
    sample = """#%PAM-1.0
auth       substack     system-auth
auth       include      postlogin
account    include      system-auth
"""
    tmp = tempfile.mkdtemp()
    try:
        target = Path(tmp) / "sudo"
        target.write_text(sample)
        original_target_files = pam_installer._target_files
        pam_installer._target_files = lambda family: [target]
        try:
            pam_installer.install_direct("fedora")
            content = target.read_text()
            assert pam_installer.PAM_BEGIN_MARKER in content
            assert "pam_exec.so" in content
            pam_installer.install_direct("fedora")  # idempotent
            assert target.read_text() == content
            pam_installer.uninstall_direct("fedora")
            assert pam_installer.PAM_BEGIN_MARKER not in target.read_text()
        finally:
            pam_installer._target_files = original_target_files
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("  ok  pam_installer round-trip")


def main() -> int:
    print("usbypass smoke tests")
    print("====================")
    test_imports()
    test_crypto()
    test_state()
    test_enrollment()
    test_usb_helpers()
    test_ui_formatters()
    test_pam_installer_marker()
    print()
    print("ALL SMOKE TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
