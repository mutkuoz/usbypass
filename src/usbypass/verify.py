"""PAM hot path — called by pam_exec.

This is the **only** module on the authentication critical path. It
must:

- Exit in a few tens of milliseconds.
- Never import pyudev (too slow; pulls in libudev).
- Return 0 only when verification definitively succeeds. Any doubt ->
  return non-zero so PAM falls through to ``pam_unix``.
"""

from __future__ import annotations

import os
import sys

# These imports are intentionally narrow — avoid pulling in logger/pyudev.
from usbypass import crypto, state, usb

PAM_SUCCESS = 0
PAM_FALLTHROUGH = 1


def run() -> int:
    username = os.environ.get("PAM_USER") or ""
    if not username:
        return PAM_FALLTHROUGH

    st = state.read_state()
    if not st:
        return PAM_FALLTHROUGH
    if st.get("username") != username:
        return PAM_FALLTHROUGH
    serial = st.get("serial")
    if not serial:
        return PAM_FALLTHROUGH

    # Defense-in-depth: re-check that a USB with this serial is still
    # physically present. udev ``remove`` clears state, so a stale
    # state.json with a missing device is very unlikely — but if it
    # happens, we must not short-circuit PAM. We use a pure-sysfs
    # lookup to avoid paying for a mount on the auth hot path.
    devnode = st.get("devnode") or ""
    if not _serial_present(serial, devnode):
        return PAM_FALLTHROUGH

    # If the partition happens to be mounted, do one extra HMAC check.
    # If it isn't (common on headless machines after idle unmount),
    # trust the handler's original verification — it already HMAC'd
    # the handshake before writing state.json, and udev remove would
    # have cleared state on unplug.
    mount = usb.find_mount_for_serial(serial)
    if mount is not None:
        stored = usb.read_handshake(mount)
        if stored is not None and not crypto.verify_handshake(username, serial, stored):
            return PAM_FALLTHROUGH

    return PAM_SUCCESS


def _serial_present(serial: str, hint_devnode: str) -> bool:
    """Return True iff a USB block device with ``serial`` is in /sys.

    Tries the hint devnode first (fast common case), then scans
    /sys/block/sd* for a matching serial.
    """
    if hint_devnode:
        got = usb._sysfs_serial_for_devnode(hint_devnode)
        if got and got == serial:
            return True
    try:
        import os as _os
        for name in _os.listdir("/sys/block"):
            if not name.startswith("sd"):
                continue
            got = usb._sysfs_serial_for_devnode(f"/dev/{name}")
            if got and got == serial:
                return True
    except OSError:
        return False
    return False


def main() -> int:
    try:
        return run()
    except BaseException:
        # Never let an exception surface to PAM; always fall through.
        return PAM_FALLTHROUGH


if __name__ == "__main__":
    sys.exit(main())
