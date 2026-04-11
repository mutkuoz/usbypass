#!/usr/bin/env bash
# USBYPASS installer.
#
# Installs the Python package, CLI shim, PAM helper, udev rule, systemd
# unit, PAM hook, and generates the host secret. Idempotent — safe to
# re-run.
#
# Usage:  sudo ./install.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

PREFIX="${USBYPASS_PREFIX:-/opt/usbypass}"
BIN_DIR="/usr/local/bin"
LIBEXEC_DIR="/usr/local/libexec"
UDEV_RULES_DIR="/etc/udev/rules.d"
SYSTEMD_DIR="/etc/systemd/system"
ETC_DIR="/etc/usbypass"
VAR_DIR="/var/lib/usbypass"

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m  %s\n' "$*" >&2; }
die()  { printf '\033[1;31mxx\033[0m  %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "install.sh must be run as root (sudo)."

detect_family() {
    if [[ ! -f /etc/os-release ]]; then
        echo "other"; return
    fi
    # shellcheck disable=SC1091
    . /etc/os-release
    local ids="${ID:-} ${ID_LIKE:-}"
    case " $ids " in
        *" debian "*|*" ubuntu "*) echo debian ;;
        *" fedora "*|*" rhel "*|*" centos "*|*" rocky "*|*" almalinux "*) echo fedora ;;
        *" arch "*|*" manjaro "*) echo arch ;;
        *) echo other ;;
    esac
}

FAMILY="$(detect_family)"
log "Detected distro family: ${FAMILY}"

command -v python3 >/dev/null || die "python3 is required."
PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
log "Using python3 ${PY_VER}"

# ---------------------------------------------------------------------------
# 1. Install pyudev (distro package preferred, pip fallback with warning)
# ---------------------------------------------------------------------------
install_pyudev() {
    if python3 -c 'import pyudev' 2>/dev/null; then
        log "pyudev already installed"
        return
    fi
    log "Installing pyudev..."
    case "$FAMILY" in
        debian)
            apt-get update -qq || true
            apt-get install -y --no-install-recommends python3-pyudev \
                || pip3 install --break-system-packages pyudev
            ;;
        fedora)
            dnf install -y python3-pyudev \
                || pip3 install --break-system-packages pyudev
            ;;
        arch)
            pacman -Sy --noconfirm python-pyudev \
                || pip3 install --break-system-packages pyudev
            ;;
        *)
            warn "Unknown distro; attempting pip install"
            pip3 install --break-system-packages pyudev
            ;;
    esac
}
install_pyudev

# ---------------------------------------------------------------------------
# 2. Copy the Python package to the prefix
# ---------------------------------------------------------------------------
log "Installing package to ${PREFIX}"
install -d -m 0755 "$PREFIX"
rm -rf "${PREFIX}/usbypass"
cp -a "${SCRIPT_DIR}/src/usbypass" "${PREFIX}/usbypass"
find "${PREFIX}/usbypass" -type d -exec chmod 0755 {} \;
find "${PREFIX}/usbypass" -type f -exec chmod 0644 {} \;

# Drop a .pth so `python3 -m usbypass` works regardless of distro pathing.
SITE="$(python3 -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
if [[ -d "$SITE" ]]; then
    echo "$PREFIX" > "${SITE}/usbypass.pth"
    log "Added ${SITE}/usbypass.pth"
else
    warn "Could not determine site-packages directory; CLI shim may fail."
fi

# ---------------------------------------------------------------------------
# 3. Install shim scripts
# ---------------------------------------------------------------------------
log "Installing shim scripts"
install -d -m 0755 "$BIN_DIR" "$LIBEXEC_DIR"
install -m 0755 "${SCRIPT_DIR}/scripts/usbypass"              "${BIN_DIR}/usbypass"
install -m 0755 "${SCRIPT_DIR}/scripts/usbypass-pam-helper"   "${LIBEXEC_DIR}/usbypass-pam-helper"
install -m 0755 "${SCRIPT_DIR}/scripts/usbypass-udev-handler" "${LIBEXEC_DIR}/usbypass-udev-handler"

# ---------------------------------------------------------------------------
# 4. Install udev rule + systemd unit
# ---------------------------------------------------------------------------
log "Installing udev rule"
install -d -m 0755 "$UDEV_RULES_DIR"
install -m 0644 "${SCRIPT_DIR}/udev/99-usbypass.rules" "${UDEV_RULES_DIR}/99-usbypass.rules"
udevadm control --reload-rules || warn "udevadm reload failed (ok in containers)"

log "Installing systemd unit"
install -d -m 0755 "$SYSTEMD_DIR"
install -m 0644 "${SCRIPT_DIR}/systemd/usbypass-clear-sudo.service" \
    "${SYSTEMD_DIR}/usbypass-clear-sudo.service"
if command -v systemctl >/dev/null; then
    systemctl daemon-reload || warn "systemctl daemon-reload failed"
fi

# ---------------------------------------------------------------------------
# 5. Create state directories and generate the host secret
# ---------------------------------------------------------------------------
log "Creating state directories"
install -d -m 0700 "$ETC_DIR"
install -d -m 0755 "$VAR_DIR"

log "Generating host secret (if missing)"
python3 - <<'PY'
from usbypass import crypto
crypto.generate_secret()
PY

# ---------------------------------------------------------------------------
# 6. Install PAM hook
# ---------------------------------------------------------------------------
log "Installing PAM hook"
"${BIN_DIR}/usbypass" install --pam-only

# ---------------------------------------------------------------------------
# 7. Summary
# ---------------------------------------------------------------------------
log "USBYPASS installation complete."
cat <<EOF

Next steps:
  1. Plug in the USB drive you want to use as a key.
  2. Mount it (most desktops do this automatically; otherwise run
     'udisksctl mount -b /dev/sdXN').
  3. Run:  sudo usbypass enroll --user \$USER
  4. Unplug and re-plug the USB, then run:  usbypass status
  5. Test:  sudo -k; sudo echo hello   (no password expected)

Security notes:
  - The host secret lives at ${ETC_DIR}/secret.key (root-only, 0600).
  - Password authentication continues to work when the USB is absent.
  - Removing the USB clears cached sudo credentials immediately.
  - See docs/security.md for the threat model.

To uninstall:  sudo ./uninstall.sh
EOF
