"""udev event handler — runs on every USB block-partition add/remove.

Invoked via ``usbypass-udev-handler <add|remove> <kernel-name>`` from
the udev RUN rule. Must exit fast (under a few hundred ms) so udev
workers aren't blocked.

Only verified insertions result in a state file being written. Removal
always clears state and kicks the sudo-credential-clear systemd unit.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from usbypass import crypto, enrollment, state, usb
from usbypass.config import SUDO_TS_DIRS
from usbypass.logger import get_logger

log = get_logger()


def handle_add(kernel_name: str) -> int:
    devnode = f"/dev/{kernel_name}"
    try:
        serial = usb.serial_for_devnode(devnode)
    except Exception as exc:
        # pyudev can trip over races at add-time; fall back to sysfs.
        log.info("pyudev lookup for %s failed (%s); trying sysfs", devnode, exc)
        serial = usb._sysfs_serial_for_devnode(devnode)
    if not serial:
        # Not a USB device we care about, or kernel hasn't populated
        # device attributes yet. Bail silently.
        return 0

    registry = enrollment.load_registry()
    matches = list(usb.iter_enrolled_matches(serial, registry))
    if not matches:
        return 0  # drive inserted but not enrolled — nothing to do

    # Give the desktop auto-mounter a shot; if nothing lands in time, we
    # will privately temp-mount the partition read-only ourselves.
    mount = usb.wait_for_mount(devnode)

    stored, trace = usb.read_handshake_diag(devnode, mount)
    if stored is None:
        log.warning(
            "USB %s (serial=%s) enrolled but handshake unreadable: %s",
            devnode,
            serial,
            trace,
        )
        return 0
    log.info("USB %s handshake source: %s", devnode, trace)

    # Pick the first matching enrolled user whose HMAC checks out.
    # In practice a single serial is tied to a single user.
    for username in matches:
        if crypto.verify_handshake(username, serial, stored):
            state.write_state(username=username, serial=serial, devnode=devnode)
            log.info(
                "USB key verified: user=%s serial=%s devnode=%s mount=%s",
                username,
                serial,
                devnode,
                mount or "<temp>",
            )
            return 0

    log.warning(
        "USB %s (serial=%s) failed HMAC verification — possible clone",
        devnode,
        serial,
    )
    return 0


def handle_remove(kernel_name: str) -> int:
    # We can't rely on udev attributes at remove time (the device is
    # already gone), so we unconditionally clear state if the currently
    # verified devnode matches or if the state file exists at all. A
    # stale state file on removal is the worst-case we're defending
    # against — lean hard toward clearing.
    st = state.read_state()
    if st is None:
        return 0

    devnode = f"/dev/{kernel_name}"
    if st.get("devnode") and st["devnode"] != devnode:
        # A different USB was unplugged; leave our state alone.
        return 0

    log.info("USB key removed (user=%s serial=%s) — clearing state", st.get("username"), st.get("serial"))
    state.clear_state()
    _trigger_sudo_clear()
    return 0


def _trigger_sudo_clear() -> None:
    """Clear sudo's credential timestamps, both directly and via systemd.

    Layer 1: call the systemd oneshot (so logging + isolation is nice).
    Layer 2: best-effort direct deletion in case systemd isn't running
    (minimal containers, rescue, etc.).
    """
    try:
        subprocess.run(
            ["systemctl", "start", "--no-block", "usbypass-clear-sudo.service"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    clear_sudo_timestamps()


def clear_sudo_timestamps() -> int:
    """Remove sudo's cached-credential timestamp files.

    Returns the number of files removed. Exposed as a module function so
    the systemd unit can call it directly via
    ``python -m usbypass clear-sudo``.
    """
    removed = 0
    for d in SUDO_TS_DIRS:
        if not d.exists():
            continue
        try:
            for entry in d.iterdir():
                # sudo's timestamp files are user-named; some builds
                # place a lockfile too. Removing everything is fine —
                # the next sudo call will re-create as needed.
                try:
                    if entry.is_file() or entry.is_symlink():
                        entry.unlink()
                        removed += 1
                except OSError:
                    continue
        except OSError:
            continue
    if removed:
        log.info("Cleared %d sudo timestamp file(s)", removed)
    return removed


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or sys.argv[1:])
    if len(argv) < 2:
        print("usage: usbypass-udev-handler <add|remove> <kernel-name>", file=sys.stderr)
        return 2
    action, kernel = argv[0], argv[1]
    try:
        if action == "add":
            return handle_add(kernel)
        if action == "remove":
            return handle_remove(kernel)
        if action == "change":
            # Treat 'change' as a re-evaluation.
            return handle_add(kernel)
    except BaseException as exc:  # never let udev see a traceback
        log.error("udev handler crashed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
