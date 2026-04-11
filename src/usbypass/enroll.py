"""`usbypass enroll` — interactive enrollment of a USB drive as a key.

Run as root. The user selects (or passes ``--device``) a USB partition.
If the desktop (or udisks2) has already mounted it, we write the
handshake there. If nothing has mounted it, we quietly temp-mount the
device read-write ourselves, write, fsync, and unmount.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable

from usbypass import crypto, enrollment, usb
from usbypass.config import RUN_DIR, TEMP_MOUNT_SUBDIR
from usbypass.logger import get_logger

log = get_logger()


class EnrollError(RuntimeError):
    pass


def _require_root() -> None:
    if os.geteuid() != 0:
        raise EnrollError("`usbypass enroll` must be run as root (sudo).")


def _prompt_choice(partitions: list[usb.UsbPartition]) -> usb.UsbPartition:
    print("Multiple USB partitions detected. Select one to enroll:")
    for i, p in enumerate(partitions, 1):
        label = p.fs_label or "(no label)"
        mp = str(p.mountpoint) if p.mountpoint else "<not mounted>"
        print(
            f"  [{i}] {p.devnode}  serial={p.serial or '?'}  "
            f"label={label}  mount={mp}  ({p.vendor or '?'} {p.model or ''})"
        )
    while True:
        choice = input(f"Choose 1-{len(partitions)}: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(partitions):
            return partitions[int(choice) - 1]
        print("Invalid selection.")


def select_partition(device: str | None) -> usb.UsbPartition:
    parts = usb.list_usb_partitions_safe()
    if not parts:
        raise EnrollError(
            "No USB partitions detected. Plug the drive in first."
        )
    if device:
        for p in parts:
            if p.devnode == device:
                return p
        raise EnrollError(f"{device} is not a USB partition I can see.")
    if len(parts) == 1:
        return parts[0]
    if not sys.stdin.isatty():
        raise EnrollError(
            "Multiple USB partitions detected and stdin is not a TTY. "
            "Pass --device /dev/sdXN explicitly."
        )
    return _prompt_choice(parts)


class _WritableMount:
    """Context manager yielding a read-write mount for ``devnode``.

    If the partition is already mounted somewhere (by udisks2, the
    desktop, etc.) we use that mountpoint and don't unmount on exit.
    Otherwise we ``mount -o rw`` it under ``/run/usbypass/mnt/`` and
    unmount on exit.
    """

    def __init__(self, part: usb.UsbPartition) -> None:
        self.part = part
        self.mountpoint: Path | None = None
        self._owned = False

    def __enter__(self) -> Path:
        if self.part.mountpoint is not None:
            self.mountpoint = self.part.mountpoint
            return self.mountpoint
        if os.geteuid() != 0:
            raise EnrollError(
                f"{self.part.devnode} is not mounted and enrollment must "
                "run as root to temp-mount it. Re-run with sudo."
            )
        parent = RUN_DIR / TEMP_MOUNT_SUBDIR
        parent.mkdir(parents=True, exist_ok=True)
        mp = Path(tempfile.mkdtemp(prefix="enroll-", dir=str(parent)))
        try:
            subprocess.run(
                ["mount", "-o", "rw,noexec,nosuid,nodev", self.part.devnode, str(mp)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
            )
        except FileNotFoundError as exc:
            try: mp.rmdir()
            except OSError: pass
            raise EnrollError("mount(8) not found on PATH") from exc
        except subprocess.CalledProcessError as exc:
            try: mp.rmdir()
            except OSError: pass
            err = (exc.stderr or b"").decode("utf-8", "replace").strip()
            raise EnrollError(
                f"could not mount {self.part.devnode}: {err or '(no output)'}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            try: mp.rmdir()
            except OSError: pass
            raise EnrollError(f"mount of {self.part.devnode} timed out") from exc
        self.mountpoint = mp
        self._owned = True
        return mp

    def __exit__(self, *exc_info) -> None:
        if self._owned and self.mountpoint is not None:
            try:
                subprocess.run(
                    ["umount", str(self.mountpoint)],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=6,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
            try:
                self.mountpoint.rmdir()
            except OSError:
                pass


def enroll(
    username: str,
    device: str | None = None,
    label: str | None = None,
    *,
    allow_weak_serial: bool = False,
) -> dict:
    """Enroll a USB drive as a bypass key for ``username``."""
    _require_root()

    part = select_partition(device)
    if not part.serial:
        raise EnrollError(
            f"{part.devnode} has no USB serial number reported by the "
            "kernel. Cheap drives often skip this. Enrollment refused."
        )
    if _is_weak_serial(part.serial) and not allow_weak_serial:
        raise EnrollError(
            f"{part.devnode} reports a weak/default serial "
            f"({part.serial!r}). Refusing to enroll — anti-clone protection "
            "would be ineffective. Re-run with --force-weak-serial if you "
            "understand the risk."
        )

    # Ensure the secret exists (install.sh normally does this).
    try:
        crypto.load_secret()
    except crypto.SecretMissingError:
        print("Host secret missing — generating a new one at /etc/usbypass/secret.key")
        crypto.generate_secret()

    payload = crypto.compute_handshake(username, part.serial)
    with _WritableMount(part) as mountpoint:
        path = usb.write_handshake(mountpoint, payload)
        # Round-trip verify — catches disk-full, read-only, or fs quirks.
        stored = usb.read_handshake(mountpoint)
        if stored != payload:
            raise EnrollError(
                f"Round-trip check failed after writing {path}. Is the drive "
                "read-only or full?"
            )
        if not crypto.verify_handshake(username, part.serial, stored):
            raise EnrollError("HMAC self-check failed — refusing to enroll.")

    entry = enrollment.add_entry(username, part.serial, label)
    log.info(
        "Enrolled USB for user=%s serial=%s label=%s devnode=%s",
        username,
        part.serial,
        entry["label"],
        part.devnode,
    )
    return {
        "username": username,
        "devnode": part.devnode,
        "serial": part.serial,
        "label": entry["label"],
        "handshake_path": str(path),
    }


def _is_weak_serial(serial: str) -> bool:
    if not serial:
        return True
    stripped = serial.strip().strip("0").strip()
    if not stripped:
        return True
    if len(serial) < 6:
        return True
    # Patterns we've seen on counterfeit sticks.
    if serial.lower() in {"0123456789abcdef", "123456789", "0000000000"}:
        return True
    return False
