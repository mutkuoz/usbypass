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

# The shim scripts now prepend /opt/usbypass to PYTHONPATH explicitly,
# so the .pth-file dance is no longer required for `python3 -m usbypass`
# to find the package. As defense-in-depth we still try to drop a .pth
# into every existing site-packages directory the running interpreter
# knows about, so direct `python3 -m usbypass ...` invocations also
# resolve. We do NOT fail the installer if every candidate is missing.
PTH_INSTALLED=0
for SITE in $(python3 -c 'import site; [print(p) for p in site.getsitepackages()]' 2>/dev/null); do
    if [[ -d "$SITE" && -w "$SITE" ]]; then
        echo "$PREFIX" > "${SITE}/usbypass.pth" || continue
        log "Added ${SITE}/usbypass.pth"
        PTH_INSTALLED=1
    fi
done
if [[ "$PTH_INSTALLED" -eq 0 ]]; then
    log "No writable site-packages found — relying on PYTHONPATH fallback in shim scripts."
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

log "Installing systemd units"
install -d -m 0755 "$SYSTEMD_DIR"
install -m 0644 "${SCRIPT_DIR}/systemd/usbypass-clear-sudo.service" \
    "${SYSTEMD_DIR}/usbypass-clear-sudo.service"
# Template unit invoked by the udev rule via SYSTEMD_WANTS — runs the
# verifier outside udev's syscall sandbox so mount(2) is allowed.
install -m 0644 "${SCRIPT_DIR}/systemd/usbypass-verify@.service" \
    "${SYSTEMD_DIR}/usbypass-verify@.service"
# Boot sweep — verifies USB keys that were already plugged in at boot,
# since /run/usbypass/state.json is tmpfs and wiped on every reboot.
install -m 0644 "${SCRIPT_DIR}/systemd/usbypass-verify-boot.service" \
    "${SYSTEMD_DIR}/usbypass-verify-boot.service"
if command -v systemctl >/dev/null; then
    systemctl daemon-reload || warn "systemctl daemon-reload failed"
    systemctl enable usbypass-verify-boot.service \
        || warn "failed to enable usbypass-verify-boot.service"
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
