#!/usr/bin/env bash
# USBYPASS uninstaller.
#
# Removes the PAM hook, udev rule, systemd unit, shim scripts, and
# installed package. The host secret and enrollment registry are left
# in place by default so that accidental re-install does not lock you
# out; pass --purge to remove them too.
#
# Usage: sudo ./uninstall.sh [--purge]

set -euo pipefail

PURGE=0
for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=1 ;;
        *) echo "unknown arg: $arg" >&2; exit 2 ;;
    esac
done

PREFIX="${USBYPASS_PREFIX:-/opt/usbypass}"
BIN_DIR="/usr/local/bin"
LIBEXEC_DIR="/usr/local/libexec"
UDEV_RULES_DIR="/etc/udev/rules.d"
SYSTEMD_DIR="/etc/systemd/system"
ETC_DIR="/etc/usbypass"
VAR_DIR="/var/lib/usbypass"
RUN_DIR="/run/usbypass"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mxx\033[0m  %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "uninstall.sh must be run as root."

# 1. Remove PAM hook (restores backups on direct-edit distros).
if [[ -x "${BIN_DIR}/usbypass" ]]; then
    log "Removing PAM hook"
    "${BIN_DIR}/usbypass" uninstall || true
fi

# 2. Remove udev rule + systemd unit.
log "Removing udev rule"
rm -f "${UDEV_RULES_DIR}/99-usbypass.rules"
command -v udevadm >/dev/null && udevadm control --reload-rules || true

log "Removing systemd units"
if command -v systemctl >/dev/null; then
    systemctl disable usbypass-verify-boot.service 2>/dev/null || true
fi
rm -f "${SYSTEMD_DIR}/usbypass-clear-sudo.service"
rm -f "${SYSTEMD_DIR}/usbypass-verify@.service"
rm -f "${SYSTEMD_DIR}/usbypass-verify-boot.service"
command -v systemctl >/dev/null && systemctl daemon-reload || true

# 3. Remove shim scripts.
log "Removing shim scripts"
rm -f "${BIN_DIR}/usbypass"
rm -f "${LIBEXEC_DIR}/usbypass-pam-helper"
rm -f "${LIBEXEC_DIR}/usbypass-udev-handler"

# 4. Remove package + .pth.
log "Removing installed package"
rm -rf "${PREFIX}/usbypass"
rmdir "${PREFIX}" 2>/dev/null || true

SITE="$(python3 -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])' 2>/dev/null || true)"
[[ -n "$SITE" && -f "${SITE}/usbypass.pth" ]] && rm -f "${SITE}/usbypass.pth"

# 5. Clear runtime state (always).
rm -rf "$RUN_DIR"

# 6. Secret + enrollment registry (only if --purge).
if [[ $PURGE -eq 1 ]]; then
    log "Purging secret and enrollment registry"
    rm -rf "$ETC_DIR" "$VAR_DIR"
else
    log "Leaving ${ETC_DIR} and ${VAR_DIR} in place (pass --purge to remove)"
fi

log "USBYPASS uninstalled."
