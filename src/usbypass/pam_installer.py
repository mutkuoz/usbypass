"""PAM stack mutation — install/uninstall our auth hook.

We support two strategies:

- **Debian/Ubuntu** (``pam-auth-update``): drop a config file in
  ``/usr/share/pam-configs/usbypass`` and invoke ``pam-auth-update
  --package``. This is the officially supported mechanism.

- **Fedora/RHEL/Arch/other**: directly edit ``/etc/pam.d/sudo`` and
  ``/etc/pam.d/system-auth`` (or ``common-auth`` equivalent), bracketed
  by marker comments so uninstall can remove exactly our lines. Backups
  are written with ``.usbypass.bak`` extensions.

The PAM line we inject:

    auth  [success=done default=ignore]  pam_exec.so quiet /usr/local/libexec/usbypass-pam-helper

``success=done`` means "USB verified → stop the stack, authentication
succeeds". ``default=ignore`` means any failure (including our helper
exiting non-zero) is invisible to PAM, and the stack falls through to
the next auth module (``pam_unix.so``), so password auth still works.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

from usbypass.config import (
    LIBEXEC_DIR,
    PAM_BEGIN_MARKER,
    PAM_CONFIGS_DIR,
    PAM_D_DIR,
    PAM_END_MARKER,
)
from usbypass.logger import get_logger

log = get_logger()

HELPER_PATH = LIBEXEC_DIR / "usbypass-pam-helper"

PAM_LINE = (
    f"auth  [success=done default=ignore]  pam_exec.so quiet {HELPER_PATH}"
)

# Files we edit on non-Debian distros.
FEDORA_TARGETS = ("sudo", "system-auth", "password-auth")
ARCH_TARGETS = ("sudo", "system-auth")


# ---------------------------------------------------------------------------
# Distro detection
# ---------------------------------------------------------------------------


def detect_family() -> str:
    """Return ``'debian'``, ``'fedora'``, ``'arch'``, or ``'other'``."""
    try:
        data = Path("/etc/os-release").read_text()
    except OSError:
        return "other"
    kv: dict[str, str] = {}
    for line in data.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            kv[k.strip()] = v.strip().strip('"')
    ids = (kv.get("ID", "") + " " + kv.get("ID_LIKE", "")).lower().split()
    if any(x in ids for x in ("debian", "ubuntu")):
        return "debian"
    if any(x in ids for x in ("fedora", "rhel", "centos", "rocky", "almalinux")):
        return "fedora"
    if any(x in ids for x in ("arch", "manjaro")):
        return "arch"
    return "other"


# ---------------------------------------------------------------------------
# Debian path — pam-auth-update
# ---------------------------------------------------------------------------


DEBIAN_PAM_CONFIG = f"""Name: USBYPASS key authentication
Default: yes
Priority: 192
Auth-Type: Primary
Auth:
\t[success=end default=ignore]\tpam_exec.so quiet {HELPER_PATH}
"""


def install_debian() -> None:
    PAM_CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
    target = PAM_CONFIGS_DIR / "usbypass"
    target.write_text(DEBIAN_PAM_CONFIG)
    os.chmod(target, 0o644)
    _run(["pam-auth-update", "--package"], check=True)
    log.info("Installed Debian pam-auth-update config at %s", target)


def uninstall_debian() -> None:
    target = PAM_CONFIGS_DIR / "usbypass"
    if target.exists():
        target.unlink()
    _run(["pam-auth-update", "--package"], check=False)
    log.info("Removed Debian pam-auth-update config")


# ---------------------------------------------------------------------------
# Direct-edit path (Fedora/Arch/other)
# ---------------------------------------------------------------------------


def _target_files(family: str) -> list[Path]:
    names: Iterable[str]
    if family == "fedora":
        names = FEDORA_TARGETS
    elif family == "arch":
        names = ARCH_TARGETS
    else:
        names = ("sudo",)
    return [PAM_D_DIR / name for name in names if (PAM_D_DIR / name).exists()]


def _pam_block() -> str:
    return (
        f"{PAM_BEGIN_MARKER}\n"
        f"{PAM_LINE}\n"
        f"{PAM_END_MARKER}\n"
    )


def install_direct(family: str) -> None:
    targets = _target_files(family)
    if not targets:
        raise RuntimeError(
            f"No PAM targets found under {PAM_D_DIR} for family={family!r}"
        )
    for target in targets:
        _inject_block(target)


def uninstall_direct(family: str) -> None:
    for target in _target_files(family):
        _remove_block(target)


def _inject_block(target: Path) -> None:
    content = target.read_text()
    if PAM_BEGIN_MARKER in content:
        log.info("%s already has USBYPASS block, skipping", target)
        return
    backup = target.with_suffix(target.suffix + ".usbypass.bak")
    if not backup.exists():
        shutil.copy2(target, backup)

    # Insert our block at the top of the auth section. The safest
    # heuristic: put it immediately before the first line whose first
    # non-comment token is "auth". If none found, prepend.
    lines = content.splitlines(keepends=True)
    insert_at = 0
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("#") or not stripped.strip():
            continue
        if stripped.split(None, 1)[:1] == ["auth"]:
            insert_at = i
            break
    new_content = "".join(lines[:insert_at]) + _pam_block() + "".join(lines[insert_at:])
    target.write_text(new_content)
    log.info("Injected USBYPASS auth line into %s (backup: %s)", target, backup)


def _remove_block(target: Path) -> None:
    if not target.exists():
        return
    content = target.read_text()
    if PAM_BEGIN_MARKER not in content:
        return
    lines = content.splitlines(keepends=True)
    out: list[str] = []
    skipping = False
    for line in lines:
        if PAM_BEGIN_MARKER in line:
            skipping = True
            continue
        if skipping and PAM_END_MARKER in line:
            skipping = False
            continue
        if skipping:
            continue
        out.append(line)
    target.write_text("".join(out))
    log.info("Removed USBYPASS block from %s", target)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def install() -> str:
    family = detect_family()
    log.info("Detected distro family: %s", family)
    if family == "debian":
        install_debian()
    else:
        install_direct(family)
    return family


def uninstall() -> str:
    family = detect_family()
    if family == "debian":
        uninstall_debian()
    else:
        uninstall_direct(family)
    return family


def _run(cmd: list[str], *, check: bool) -> None:
    try:
        subprocess.run(cmd, check=check)
    except FileNotFoundError as exc:
        if check:
            raise RuntimeError(f"Required command not found: {cmd[0]}") from exc
