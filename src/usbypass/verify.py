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
    # mounted and still carries a matching handshake. This catches the
    # case where the state file was left stale (e.g. udev remove hook
    # lost a race) and the user has since swapped keys.
    mount = usb.find_mount_for_serial(serial)
    if mount is None:
        return PAM_FALLTHROUGH
    stored = usb.read_handshake(mount)
    if stored is None:
        return PAM_FALLTHROUGH
    if not crypto.verify_handshake(username, serial, stored):
        return PAM_FALLTHROUGH

    return PAM_SUCCESS


def main() -> int:
    try:
        return run()
    except BaseException:
        # Never let an exception surface to PAM; always fall through.
        return PAM_FALLTHROUGH


if __name__ == "__main__":
    sys.exit(main())
