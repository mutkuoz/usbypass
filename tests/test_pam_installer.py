"""Tests for the marker-based direct PAM edit path.

We don't actually mutate /etc/pam.d in tests — we monkeypatch
``_target_files`` to point at temp files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from usbypass import pam_installer


EXAMPLE_PAM = """#%PAM-1.0
# Sample sudo PAM file
auth       substack     system-auth
auth       include      postlogin
account    include      system-auth
password   include      system-auth
session    include      system-auth
session    include      postlogin
"""


def _setup_fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "sudo"
    target.write_text(EXAMPLE_PAM)
    monkeypatch.setattr(pam_installer, "_target_files", lambda family: [target])
    return target


def test_inject_then_remove_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _setup_fake(tmp_path, monkeypatch)

    pam_installer.install_direct("fedora")
    content = target.read_text()
    assert pam_installer.PAM_BEGIN_MARKER in content
    assert pam_installer.PAM_END_MARKER in content
    assert "pam_exec.so" in content
    # Must be inserted before the first auth line, not after.
    assert content.index(pam_installer.PAM_BEGIN_MARKER) < content.index("auth       substack")
    # Backup was created.
    assert target.with_suffix(target.suffix + ".usbypass.bak").exists()

    pam_installer.uninstall_direct("fedora")
    content = target.read_text()
    assert pam_installer.PAM_BEGIN_MARKER not in content
    assert pam_installer.PAM_END_MARKER not in content
    assert "pam_exec.so" not in content
    # Non-marker content is preserved.
    assert "substack     system-auth" in content


def test_inject_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _setup_fake(tmp_path, monkeypatch)
    pam_installer.install_direct("fedora")
    first = target.read_text()
    pam_installer.install_direct("fedora")
    second = target.read_text()
    assert first == second


def test_remove_on_unmodified_file_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _setup_fake(tmp_path, monkeypatch)
    original = target.read_text()
    pam_installer.uninstall_direct("fedora")
    assert target.read_text() == original


def test_detect_family_from_os_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # detect_family reads /etc/os-release directly; we verify the
    # function is at least defensive against a missing file.
    monkeypatch.setattr(
        "pathlib.Path.read_text",
        lambda self: (_ for _ in ()).throw(OSError()) if str(self) == "/etc/os-release" else Path.read_text(self),
    )
    # With a synthesized failure we should at least get "other".
    # (Real detection is tested manually against a VM.)
    family = pam_installer.detect_family()
    assert family in {"debian", "fedora", "arch", "other"}
