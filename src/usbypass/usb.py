"""USB detection, serial lookup, mount discovery, handshake I/O.

All pyudev use is isolated to this module so the PAM hot path can
import a minimal subset. The ``find_mount_for_serial`` fast path does
not require pyudev — it reads ``/proc/self/mountinfo`` and
``/sys/block/.../device/...`` directly.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from usbypass.config import (
    MOUNT_WAIT_INTERVAL_S,
    MOUNT_WAIT_TIMEOUT_S,
    RUN_DIR,
    TEMP_MOUNT_SUBDIR,
    USB_HANDSHAKE_REL,
)


@dataclass(frozen=True)
class UsbPartition:
    devnode: str               # e.g. /dev/sdb1
    parent_devnode: str        # e.g. /dev/sdb
    serial: str                # ID_SERIAL_SHORT from udev
    fs_uuid: str | None        # ID_FS_UUID
    fs_label: str | None       # ID_FS_LABEL
    fs_type: str | None        # ID_FS_TYPE (e.g. "exfat", "vfat", "ntfs")
    vendor: str | None
    model: str | None
    mountpoint: Path | None
    size_bytes: int | None     # partition size in bytes, from sysfs


# ---------------------------------------------------------------------------
# Enumeration (used by `usbypass enroll`, `usbypass list`, etc.)
# ---------------------------------------------------------------------------


def list_usb_partitions() -> list[UsbPartition]:
    """Return every USB block partition currently visible to the kernel."""
    import pyudev  # lazy import: PAM hot path must not pull this in

    ctx = pyudev.Context()
    mounts = _read_mountinfo()
    out: list[UsbPartition] = []
    for dev in ctx.list_devices(subsystem="block", DEVTYPE="partition"):
        if dev.get("ID_BUS") != "usb":
            continue
        serial = dev.get("ID_SERIAL_SHORT") or dev.get("ID_SERIAL") or ""
        parent = dev.find_parent("block", "disk")
        parent_node = parent.device_node if parent is not None else ""
        mp = mounts.get(dev.device_node)
        out.append(
            UsbPartition(
                devnode=dev.device_node,
                parent_devnode=parent_node,
                serial=serial,
                fs_uuid=dev.get("ID_FS_UUID"),
                fs_label=dev.get("ID_FS_LABEL"),
                fs_type=dev.get("ID_FS_TYPE"),
                vendor=dev.get("ID_VENDOR"),
                model=dev.get("ID_MODEL"),
                mountpoint=Path(mp) if mp else None,
                size_bytes=_sysfs_partition_size_bytes(dev.device_node),
            )
        )
    return out


def _sysfs_partition_size_bytes(devnode: str) -> int | None:
    """Read the partition size (in bytes) from sysfs.

    sysfs stores the size in 512-byte sectors at
    ``/sys/class/block/<name>/size``.
    """
    name = os.path.basename(devnode)
    path = Path("/sys/class/block") / name / "size"
    try:
        sectors = int(path.read_text().strip())
    except (OSError, ValueError):
        return None
    return sectors * 512


def serial_for_devnode(devnode: str) -> str | None:
    """Return the USB controller serial for a given /dev/sdXN node."""
    import pyudev

    ctx = pyudev.Context()
    try:
        dev = pyudev.Devices.from_device_file(ctx, devnode)
    except (pyudev.DeviceNotFoundByFileError, OSError):
        return None
    return dev.get("ID_SERIAL_SHORT") or dev.get("ID_SERIAL")


# ---------------------------------------------------------------------------
# Fast path (no pyudev)
# ---------------------------------------------------------------------------


def _read_mountinfo() -> dict[str, str]:
    """Map /dev/sdXN -> first mountpoint from /proc/self/mountinfo.

    This avoids shelling out to findmnt and respects mount namespaces.
    """
    mounts: dict[str, str] = {}
    try:
        with open("/proc/self/mountinfo", "r") as f:
            for line in f:
                # Format: see proc(5) for mountinfo fields.
                parts = line.split(" ")
                try:
                    sep = parts.index("-")
                except ValueError:
                    continue
                if sep + 2 >= len(parts):
                    continue
                mountpoint = _unescape_mountinfo(parts[4])
                source = _unescape_mountinfo(parts[sep + 2])
                if source.startswith("/dev/") and source not in mounts:
                    mounts[source] = mountpoint
    except OSError:
        pass
    return mounts


def _unescape_mountinfo(field: str) -> str:
    # mountinfo escapes space/tab/newline/backslash as \040 \011 \012 \134
    return (
        field.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def find_mount_for_serial(serial: str) -> Path | None:
    """Locate the mountpoint of a USB partition with the given serial.

    Uses /sys directly to avoid the import cost of pyudev on the hot
    path. Returns None if the device isn't mounted (or not present).
    """
    if not serial:
        return None
    mounts = _read_mountinfo()
    for devnode in mounts:
        sysserial = _sysfs_serial_for_devnode(devnode)
        if sysserial and sysserial == serial:
            return Path(mounts[devnode])
    return None


def _sysfs_serial_for_devnode(devnode: str) -> str | None:
    """Walk /sys/block/<parent>/device/... looking for the USB serial.

    sysfs exposes the USB device descriptor serial at
    ``/sys/block/<disk>/device/../../serial`` for mass-storage devices.
    """
    if not devnode.startswith("/dev/"):
        return None
    name = os.path.basename(devnode)
    # Strip trailing digits to find the parent disk: sdb1 -> sdb, nvme0n1p1 -> nvme0n1
    parent = name
    while parent and parent[-1].isdigit():
        parent = parent[:-1]
    if parent.endswith("p"):  # nvme0n1p -> nvme0n1
        parent = parent[:-1]
    sys_block = Path("/sys/block") / parent
    if not sys_block.exists():
        # Fallback: try the partition's own sysfs entry
        sys_block = Path("/sys/class/block") / name
    device_link = sys_block / "device"
    if not device_link.exists():
        return None
    try:
        device_real = device_link.resolve()
    except OSError:
        return None
    # Walk up looking for a `serial` file (the USB device-level serial).
    cur = device_real
    for _ in range(6):
        cand = cur / "serial"
        if cand.is_file():
            try:
                return cand.read_text().strip() or None
            except OSError:
                return None
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


# ---------------------------------------------------------------------------
# Handshake file I/O
# ---------------------------------------------------------------------------


def read_handshake(mountpoint: Path) -> bytes | None:
    """Read the handshake file from a mounted USB partition."""
    p = Path(mountpoint) / USB_HANDSHAKE_REL
    try:
        with open(p, "rb") as f:
            return f.read()
    except (FileNotFoundError, PermissionError, OSError):
        return None


def write_handshake(mountpoint: Path, payload: bytes) -> Path:
    """Write the handshake file to a mounted USB partition and fsync it."""
    p = Path(mountpoint) / USB_HANDSHAKE_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, p)
    # Best-effort: flush the directory too.
    try:
        dirfd = os.open(str(p.parent), os.O_RDONLY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    except OSError:
        pass
    return p


# ---------------------------------------------------------------------------
# udev add-handler helper
# ---------------------------------------------------------------------------


def wait_for_mount(devnode: str, timeout: float = MOUNT_WAIT_TIMEOUT_S) -> Path | None:
    """Poll mountinfo for up to ``timeout`` seconds waiting for a mount.

    udev RUN workers must not block indefinitely. This function enforces
    a hard upper bound.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        mounts = _read_mountinfo()
        mp = mounts.get(devnode)
        if mp:
            return Path(mp)
        time.sleep(MOUNT_WAIT_INTERVAL_S)
    return None


def iter_enrolled_matches(serial: str, enrolled: dict) -> Iterator[str]:
    """Yield usernames whose enrolled serials include ``serial``."""
    for username, entries in enrolled.items():
        for entry in entries:
            if entry.get("serial") == serial:
                yield username
                break


# ---------------------------------------------------------------------------
# Private read-only temp-mount
# ---------------------------------------------------------------------------
#
# When the udev handler fires (or `usbypass verify-now` runs) on a headless
# box, nothing may have auto-mounted the USB. We can still do our job by
# privately mounting the partition read-only, reading the handshake, and
# unmounting — all without polluting the user's mountinfo.
#
# We use subprocess(mount/umount) instead of libmount/ctypes because it
# gives us free filesystem-type autodetection and the external commands
# know how to negotiate with any helper binaries on the host.


class TempMountError(RuntimeError):
    """Raised when a read-only temp-mount cannot be established."""


@dataclass
class TempMount:
    devnode: str
    mountpoint: Path
    _mounted: bool = True

    def unmount(self) -> None:
        if not self._mounted:
            return
        try:
            subprocess.run(
                ["umount", "--lazy", str(self.mountpoint)],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=4,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        self._mounted = False
        # Best-effort cleanup of the empty dir.
        try:
            self.mountpoint.rmdir()
        except OSError:
            pass


@contextlib.contextmanager
def temp_mount_readonly(devnode: str) -> Iterator[TempMount]:
    """Mount ``devnode`` read-only in a private temp dir.

    Yields a :class:`TempMount` and always unmounts on exit. Requires
    root. Raises :class:`TempMountError` if the mount fails for any
    reason — caller should treat that as "can't verify, fall through".
    """
    if os.geteuid() != 0:
        raise TempMountError("temp_mount_readonly requires root")

    parent = RUN_DIR / TEMP_MOUNT_SUBDIR
    try:
        parent.mkdir(parents=True, exist_ok=True)
        os.chmod(parent, 0o755)
    except OSError as exc:
        raise TempMountError(f"cannot create {parent}: {exc}") from exc

    mp = Path(tempfile.mkdtemp(prefix="usbypass-", dir=str(parent)))
    try:
        os.chmod(mp, 0o755)
    except OSError:
        pass

    # Try mount(8) with filesystem autodetection and RO. On a mis-
    # typed / encrypted / damaged partition this will exit non-zero
    # quickly, which is fine — we'll just refuse to verify.
    try:
        result = subprocess.run(
            ["mount", "-o", "ro,noexec,nosuid,nodev", devnode, str(mp)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=8,
        )
    except FileNotFoundError as exc:
        try:
            mp.rmdir()
        except OSError:
            pass
        raise TempMountError("mount(8) not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        try:
            mp.rmdir()
        except OSError:
            pass
        raise TempMountError(f"mount of {devnode} timed out") from exc

    if result.returncode != 0:
        err = (result.stderr or b"").decode("utf-8", "replace").strip()
        try:
            mp.rmdir()
        except OSError:
            pass
        raise TempMountError(
            f"mount of {devnode} failed (rc={result.returncode}): {err or '(no output)'}"
        )

    tm = TempMount(devnode=devnode, mountpoint=mp)
    try:
        yield tm
    finally:
        tm.unmount()


def read_handshake_any(devnode: str, known_mountpoint: Path | None) -> bytes | None:
    """Fetch the handshake for ``devnode``, auto-mounting if necessary.

    Preference order:
      1. ``known_mountpoint`` (if provided and the file is there).
      2. Whatever mountpoint ``/proc/self/mountinfo`` reports for this devnode.
      3. A private read-only temp-mount (requires root).

    Returns ``None`` if no handshake can be read by any path.
    """
    if known_mountpoint is not None:
        blob = read_handshake(known_mountpoint)
        if blob is not None:
            return blob

    mounts = _read_mountinfo()
    mp = mounts.get(devnode)
    if mp:
        blob = read_handshake(Path(mp))
        if blob is not None:
            return blob

    if os.geteuid() != 0:
        return None

    try:
        with temp_mount_readonly(devnode) as tm:
            return read_handshake(tm.mountpoint)
    except TempMountError:
        return None


# ---------------------------------------------------------------------------
# pyudev-free enumeration (fallback for when pyudev isn't installed)
# ---------------------------------------------------------------------------


def list_usb_partitions_sysfs() -> list[UsbPartition]:
    """Enumerate USB partitions using only /sys and /proc — no pyudev.

    Coarser than :func:`list_usb_partitions` (vendor/model are looked up
    from sysfs rather than udev's ID_* database) but works in minimal
    environments and as a fallback when the pyudev import fails.
    """
    mounts = _read_mountinfo()
    out: list[UsbPartition] = []
    sys_block = Path("/sys/block")
    if not sys_block.is_dir():
        return out
    for disk in sorted(sys_block.iterdir()):
        # Skip loop, ram, dm, zram, nvme — we only want sdXN.
        if not disk.name.startswith("sd"):
            continue
        device_link = disk / "device"
        if not device_link.exists():
            continue
        # Only USB-backed disks: the device path must contain a usbN hop.
        try:
            real = device_link.resolve()
        except OSError:
            continue
        if "/usb" not in str(real):
            continue
        serial = _sysfs_serial_for_devnode(f"/dev/{disk.name}") or ""
        vendor = _read_sysfs_text(real / "vendor")
        model = _read_sysfs_text(real / "model")
        for part in sorted(disk.iterdir()):
            if not part.is_dir():
                continue
            if not part.name.startswith(disk.name):
                continue
            devnode = f"/dev/{part.name}"
            mp = mounts.get(devnode)
            out.append(
                UsbPartition(
                    devnode=devnode,
                    parent_devnode=f"/dev/{disk.name}",
                    serial=serial,
                    fs_uuid=None,
                    fs_label=None,
                    fs_type=None,
                    vendor=vendor,
                    model=model,
                    mountpoint=Path(mp) if mp else None,
                    size_bytes=_sysfs_partition_size_bytes(devnode),
                )
            )
    return out


def list_usb_partitions_safe() -> list[UsbPartition]:
    """Try the pyudev-backed enumerator and fall back to sysfs on failure."""
    try:
        return list_usb_partitions()
    except Exception:
        return list_usb_partitions_sysfs()


def _read_sysfs_text(path: Path) -> str | None:
    try:
        return path.read_text().strip() or None
    except OSError:
        return None
