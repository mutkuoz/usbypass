"""Central configuration and filesystem paths for USBYPASS.

Having every path in one module keeps install/uninstall symmetric and
lets tests monkeypatch locations cleanly.
"""

from __future__ import annotations

import os
from pathlib import Path

# Root-owned persistent state
ETC_DIR = Path(os.environ.get("USBYPASS_ETC_DIR", "/etc/usbypass"))
SECRET_PATH = ETC_DIR / "secret.key"

VAR_DIR = Path(os.environ.get("USBYPASS_VAR_DIR", "/var/lib/usbypass"))
ENROLLED_PATH = VAR_DIR / "enrolled.json"

# Volatile runtime state (tmpfs on systemd distros)
RUN_DIR = Path(os.environ.get("USBYPASS_RUN_DIR", "/run/usbypass"))
STATE_FILE = RUN_DIR / "state.json"

# Per-USB layout
USB_HANDSHAKE_REL = Path(".usbypass/handshake")
USB_META_REL = Path(".usbypass/meta.json")

# Install targets
INSTALL_PREFIX = Path(os.environ.get("USBYPASS_PREFIX", "/opt/usbypass"))
BIN_DIR = Path("/usr/local/bin")
LIBEXEC_DIR = Path("/usr/local/libexec")
UDEV_RULES_DIR = Path("/etc/udev/rules.d")
SYSTEMD_SYSTEM_DIR = Path("/etc/systemd/system")
PAM_D_DIR = Path("/etc/pam.d")
PAM_CONFIGS_DIR = Path("/usr/share/pam-configs")

# Sudo timestamp directories — varies by distro/sudo build.
# We clear whichever ones exist on remove.
SUDO_TS_DIRS = (
    Path("/run/sudo/ts"),
    Path("/var/run/sudo/ts"),
    Path("/var/db/sudo"),
)

# Markers bracketing direct PAM edits so uninstall can find & remove them.
PAM_BEGIN_MARKER = "# >>> USBYPASS BEGIN (do not edit inside this block)"
PAM_END_MARKER = "# <<< USBYPASS END"

# How long the udev add-handler will poll for a mount to appear before
# falling back to a private temp-mount. udisks2 auto-mount of exfat can
# take 1–3s, and we want to wait long enough to let the desktop win on
# workstation installs — but not so long udev kills us.
MOUNT_WAIT_TIMEOUT_S = 4.0
MOUNT_WAIT_INTERVAL_S = 0.05

# Where the handler puts its private read-only temp-mount when no
# auto-mount appears. Kept under RUN_DIR so it disappears on reboot.
TEMP_MOUNT_SUBDIR = "mnt"

LOG_TAG = "usbypass"
