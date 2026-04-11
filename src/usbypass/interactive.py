"""Interactive CLI — run `usbypass` with no args to land here.

Curses-free, line-oriented TUI that works over SSH, in rescue shells,
and on every distro USBYPASS targets. The look & feel is deliberately
chunky and verbose so it explains itself to a first-time user.

Layout:

  ╭─ USBYPASS · physical USB key for Linux PAM ──────────╮
  │
  │  ● Active key: <user> [<serial>] via <devnode>
  │    verified <relative-time>
  │
  │  ━━ USB devices ━━
  │
  │  [1] ● /dev/sdX1   <vendor> <model>
  │      <size> · <fs> · <label>
  │      serial: <serial>
  │      mount:  <mount or ‹not mounted›>
  │      tags:   [enrolled: alice] [VERIFIED]
  │
  │  ━━ actions ━━
  │
  │  [1-N] select device   [e] enroll new   [r] refresh
  │  [s] status snapshot   [l] list keys    [d] doctor
  │  [h] help              [q] quit
  ╰────────────────────────────────────────────────────────╯
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from usbypass import enrollment, state, usb, ui


# ---------------------------------------------------------------------------
# Row aggregation
# ---------------------------------------------------------------------------


@dataclass
class UsbRow:
    index: int
    part: usb.UsbPartition
    enrolled_by: list[str]
    verified_now: bool


def _gather_rows() -> list[UsbRow]:
    parts = usb.list_usb_partitions_safe()
    registry = enrollment.load_registry()
    st = state.read_state() or {}
    verified_serial = st.get("serial")
    rows: list[UsbRow] = []
    for i, part in enumerate(parts, 1):
        enrolled_by = [
            user
            for user, entries in registry.items()
            if any(e.get("serial") == part.serial for e in entries)
        ]
        rows.append(
            UsbRow(
                index=i,
                part=part,
                enrolled_by=enrolled_by,
                verified_now=bool(verified_serial and verified_serial == part.serial),
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------


def _print_header() -> None:
    width = ui.term_width()
    print()
    print(ui.box_top("USBYPASS · physical USB key for Linux PAM", width))
    print(ui.box_bottom(width))


def _print_state_banner() -> None:
    st = state.read_state()
    if st:
        line = (
            f"  {ui.OK(ui.GLYPH_DOT_ON)} {ui.bold('Active key')}  "
            f"{ui.bold(str(st.get('username') or '?'))} "
            f"{ui.dim('[' + str(st.get('serial') or '?') + ']')} "
            f"{ui.dim('via')} {st.get('devnode') or '?'}"
        )
        print(line)
        ts = st.get("verified_at")
        if isinstance(ts, (int, float)):
            print(ui.dim(f"      verified {ui.fmt_relative(ts)}  ({ui.fmt_absolute(ts)})"))
    else:
        print(f"  {ui.MUTED(ui.GLYPH_DOT_OFF)} {ui.MUTED('No active USB key — password auth in effect')}")
    print()


def _format_row(row: UsbRow) -> list[str]:
    p = row.part
    vendor = (p.vendor or "").replace("_", " ").strip()
    model = (p.model or "").replace("_", " ").strip()
    desc = (vendor + " " + model).strip() or "Unknown USB device"

    label = p.fs_label or "(no label)"
    size = ui.fmt_bytes(p.size_bytes)
    fs = p.fs_type or "?"
    serial = p.serial or ui.WARN("(no serial!)")
    mp = str(p.mountpoint) if p.mountpoint else ui.MUTED("‹not mounted›")

    # Tag line
    tags: list[str] = []
    if row.verified_now:
        tags.append(ui.OK(f"{ui.GLYPH_CHECK} VERIFIED"))
    if row.enrolled_by:
        who = ", ".join(row.enrolled_by)
        tags.append(ui.INFO(f"{ui.GLYPH_KEY} enrolled: {who}"))
    elif p.serial:
        tags.append(ui.MUTED("not enrolled"))
    tag_str = "  ".join(tags)

    dot = (
        ui.OK(ui.GLYPH_DOT_ON)
        if row.verified_now
        else (ui.INFO(ui.GLYPH_DOT_ON) if row.enrolled_by else ui.MUTED(ui.GLYPH_DOT_OFF))
    )
    idx = ui.KEY(f"[{row.index}]")

    lines = [
        f"  {idx} {dot} {ui.bold(p.devnode)}   {desc}",
        f"      {ui.dim('size:')}  {size}   {ui.dim('fs:')} {fs}   {ui.dim('label:')} {label}",
        f"      {ui.dim('serial:')} {serial}",
        f"      {ui.dim('mount:')}  {mp}",
    ]
    if tag_str:
        lines.append(f"      {tag_str}")
    return lines


def _print_usb_table(rows: list[UsbRow]) -> None:
    print(ui.section("USB devices"))
    print()
    if not rows:
        print(f"  {ui.WARN('No USB partitions currently attached.')}")
        print(f"  {ui.MUTED('Plug a drive in, then press [r] to refresh.')}")
        print()
        return
    for i, row in enumerate(rows):
        for line in _format_row(row):
            print(line)
        if i != len(rows) - 1:
            print()
    print()


def _print_actions(have_devices: bool) -> None:
    print(ui.section("actions"))
    print()
    cols = []
    if have_devices:
        cols.append(f"  {ui.KEY('[1-N]')} select device")
    cols.append(f"  {ui.KEY('[e]')} enroll wizard")
    cols.append(f"  {ui.KEY('[r]')} refresh")
    print("   ".join(cols))
    print(
        f"  {ui.KEY('[s]')} status snapshot     "
        f"{ui.KEY('[l]')} list enrolled       "
        f"{ui.KEY('[d]')} doctor"
    )
    print(
        f"  {ui.KEY('[h]')} help                "
        f"{ui.KEY('[q]')} quit"
    )
    print()


# ---------------------------------------------------------------------------
# Prompt primitives
# ---------------------------------------------------------------------------


def _prompt(msg: str) -> str:
    try:
        return input(msg).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return ""


def _confirm(msg: str, *, default: bool = False) -> bool:
    suffix = " [Y/n]" if default else " [y/N]"
    while True:
        raw = _prompt(f"  {msg}{suffix} ").lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print(f"  {ui.ERR('please answer y or n')}")


def _press_enter() -> None:
    _prompt(ui.dim("  press enter to continue… "))


# ---------------------------------------------------------------------------
# Privileged-action helpers
# ---------------------------------------------------------------------------


def _is_root() -> bool:
    return os.geteuid() == 0


def _require_root(action: str) -> bool:
    if _is_root():
        return True
    print()
    print(f"  {ui.ERR(ui.GLYPH_LOCK + ' ' + action + ' requires root.')}")
    print(f"  {ui.MUTED('Quit, then re-run as: ')}{ui.bold('sudo usbypass')}")
    return False


def _resolve_target_user() -> str:
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        return sudo_user
    return os.environ.get("USER", "root")


# ---------------------------------------------------------------------------
# Per-USB detail view
# ---------------------------------------------------------------------------


def _print_device_detail(row: UsbRow) -> None:
    p = row.part
    width = ui.term_width()
    title = f"{p.devnode} · {(p.vendor or '').strip()} {(p.model or '').strip()}".strip()
    print()
    print(ui.box_top(title, width))
    print(ui.box_bottom(width))
    print()
    print(ui.kv("devnode",     ui.bold(p.devnode)))
    print(ui.kv("parent",      p.parent_devnode or "?"))
    print(ui.kv("serial",      p.serial or ui.WARN("(none)")))
    print(ui.kv("vendor",      p.vendor or "?"))
    print(ui.kv("model",       p.model or "?"))
    print(ui.kv("fs type",     p.fs_type or "?"))
    print(ui.kv("fs label",    p.fs_label or "?"))
    print(ui.kv("fs uuid",     p.fs_uuid or "?"))
    print(ui.kv("size",        ui.fmt_bytes(p.size_bytes)))
    print(ui.kv("mountpoint",  str(p.mountpoint) if p.mountpoint else ui.MUTED("‹not mounted›")))

    # Anti-clone strength
    strength = _serial_strength(p.serial)
    print(ui.kv("anti-clone",  strength))

    # Enrollment
    if row.enrolled_by:
        names = ", ".join(row.enrolled_by)
        print(ui.kv("enrolled",    ui.OK(f"yes — for {names}")))
    else:
        print(ui.kv("enrolled",    ui.MUTED("no")))

    # Verification
    if row.verified_now:
        st = state.read_state() or {}
        ts = st.get("verified_at")
        when = ui.fmt_relative(ts) if isinstance(ts, (int, float)) else "?"
        print(ui.kv("verified",    ui.OK(f"{ui.GLYPH_CHECK} active ({when})")))
    else:
        print(ui.kv("verified",    ui.MUTED("no — run `verify now` from this menu")))
    print()


def _serial_strength(serial: str | None) -> str:
    if not serial:
        return ui.ERR(f"{ui.GLYPH_CROSS} no serial — anti-clone disabled")
    if len(serial) < 6:
        return ui.WARN(f"{ui.GLYPH_WARN} weak ({len(serial)} chars)")
    if serial.lower() in {"0123456789abcdef", "123456789", "0000000000"}:
        return ui.WARN(f"{ui.GLYPH_WARN} default-pattern serial")
    return ui.OK(f"{ui.GLYPH_CHECK} strong serial ({len(serial)} chars)")


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def _act_enroll(row: UsbRow) -> None:
    if not _require_root("Enrolling"):
        return
    from usbypass.enroll import EnrollError, enroll as do_enroll, _is_weak_serial

    print()
    print(ui.section("enroll wizard"))
    print()
    print(ui.kv("device", ui.bold(row.part.devnode)))
    print(ui.kv("serial", row.part.serial or ui.WARN("(none)")))
    print(ui.kv("model",  f"{row.part.vendor or '?'} {row.part.model or ''}".strip()))
    print()

    default_user = _resolve_target_user()
    user = _prompt(f"  Target username {ui.dim(f'[default: {default_user}]')}: ") or default_user
    label = _prompt(f"  Friendly label  {ui.dim('[blank = auto]')}: ") or None

    force = False
    if row.part.serial and _is_weak_serial(row.part.serial):
        print()
        print(f"  {ui.WARN(ui.GLYPH_WARN + ' Weak/default serial detected — anti-clone protection will be ineffective.')}")
        if not _confirm("Enroll anyway?", default=False):
            print(f"  {ui.MUTED('aborted.')}")
            return
        force = True

    print()
    if not _confirm(
        f"Write USBYPASS handshake to {ui.bold(row.part.devnode)} for user {ui.bold(user)}?",
        default=True,
    ):
        print(f"  {ui.MUTED('aborted.')}")
        return

    try:
        result = do_enroll(
            username=user,
            device=row.part.devnode,
            label=label,
            allow_weak_serial=force,
        )
    except EnrollError as exc:
        print()
        print(f"  {ui.ERR(ui.GLYPH_CROSS + ' enroll failed: ' + str(exc))}")
        return
    print()
    print(f"  {ui.OK(ui.GLYPH_CHECK + ' enrolled successfully!')}")
    for k, v in result.items():
        print(ui.kv(k, str(v)))
    print()
    print(f"  {ui.MUTED('Pick [v] verify-now from the device menu, or unplug + replug the USB.')}")


def _act_revoke(row: UsbRow) -> None:
    if not row.enrolled_by:
        print(f"  {ui.WARN('This USB is not enrolled.')}")
        return
    if not _require_root("Revoking"):
        return
    print()
    if not _confirm(
        f"Revoke enrollment of {ui.bold(row.part.serial)} for {', '.join(row.enrolled_by)}?",
        default=False,
    ):
        return
    for user in row.enrolled_by:
        if enrollment.remove_entry(user, row.part.serial):
            print(f"  {ui.OK(ui.GLYPH_CHECK + f' revoked for {user}')}")


def _act_verify_now(row: UsbRow) -> None:
    if not _require_root("Verification"):
        return
    from usbypass import crypto

    serial = row.part.serial
    if not serial:
        print(f"  {ui.ERR('no serial — cannot verify')}")
        return
    registry = enrollment.load_registry()
    matches = list(usb.iter_enrolled_matches(serial, registry))
    if not matches:
        print(f"  {ui.WARN('not enrolled — nothing to verify')}")
        return

    print(f"  {ui.MUTED('reading handshake (auto-mounting if needed)...')}")
    stored = usb.read_handshake_any(row.part.devnode, row.part.mountpoint)
    if stored is None:
        print(f"  {ui.ERR(ui.GLYPH_CROSS + ' could not read handshake (mount + temp-mount both failed)')}")
        return

    for user in matches:
        if crypto.verify_handshake(user, serial, stored):
            state.write_state(username=user, serial=serial, devnode=row.part.devnode)
            print(f"  {ui.OK(ui.GLYPH_CHECK + f' verified! active user = {user}')}")
            print(f"  {ui.MUTED('You can now `sudo` (and log in) without a password while this USB is plugged in.')}")
            return
    print(f"  {ui.ERR(ui.GLYPH_CROSS + ' handshake did not match any enrolled user — possible clone')}")


def _act_unmount(row: UsbRow) -> None:
    if row.part.mountpoint is None:
        print(f"  {ui.MUTED('Already not mounted.')}")
        return
    if not _require_root("Unmounting"):
        return
    import subprocess
    try:
        result = subprocess.run(
            ["umount", str(row.part.mountpoint)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=8,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"  {ui.ERR('umount failed: ' + str(exc))}")
        return
    if result.returncode == 0:
        print(f"  {ui.OK(ui.GLYPH_CHECK + ' unmounted')}")
    else:
        err = (result.stderr or b"").decode("utf-8", "replace").strip()
        print(f"  {ui.ERR('umount failed: ' + (err or 'rc=' + str(result.returncode)))}")


# ---------------------------------------------------------------------------
# Per-USB submenu
# ---------------------------------------------------------------------------


def _per_usb_menu(row: UsbRow) -> None:
    while True:
        ui.clear_screen()
        _print_device_detail(row)
        print(ui.section("device actions"))
        print()
        print(f"  {ui.KEY('[e]')} enroll this USB")
        print(f"  {ui.KEY('[v]')} verify now")
        print(f"  {ui.KEY('[r]')} revoke enrollment")
        print(f"  {ui.KEY('[u]')} unmount")
        print(f"  {ui.KEY('[i]')} refresh details")
        print(f"  {ui.KEY('[b]')} back to main menu")
        print(f"  {ui.KEY('[q]')} quit")
        print()
        choice = _prompt(f"  {ui.ACCENT(ui.GLYPH_ARROW)} action: ").lower()
        if choice in {"e", "enroll"}:
            _act_enroll(row)
            _press_enter()
        elif choice in {"v", "verify"}:
            _act_verify_now(row)
            _press_enter()
        elif choice in {"r", "revoke"}:
            _act_revoke(row)
            _press_enter()
        elif choice in {"u", "umount", "unmount"}:
            _act_unmount(row)
            _press_enter()
        elif choice in {"i", "info", "refresh"}:
            # Reload row from a fresh snapshot.
            new_rows = _gather_rows()
            replacement = next(
                (r for r in new_rows if r.part.devnode == row.part.devnode),
                None,
            )
            if replacement is None:
                print(f"  {ui.WARN('device disappeared')}")
                _press_enter()
                return
            row = replacement
        elif choice in {"b", "back", ""}:
            return
        elif choice in {"q", "quit", "exit"}:
            raise SystemExit(0)
        else:
            print(f"  {ui.ERR('unknown action: ' + repr(choice))}")
            _press_enter()


# ---------------------------------------------------------------------------
# Top-level views
# ---------------------------------------------------------------------------


def _print_help() -> None:
    ui.clear_screen()
    width = ui.term_width()
    print()
    print(ui.box_top("USBYPASS help", width))
    print(ui.box_bottom(width))
    print()
    print(ui.bold("  What is this?"))
    print("    USBYPASS turns a physical USB drive into a password-bypass key")
    print("    for Linux PAM. When the enrolled USB is plugged in, sudo and")
    print("    login skip the password prompt. When it is absent, the system")
    print("    behaves like an ordinary password-protected box.")
    print()
    print(ui.bold("  How does it work?"))
    print("    Enrollment writes a handshake file to the USB:")
    print(f"      {ui.dim('handshake = HMAC-SHA256(host_secret, username:usb_serial)')}")
    print("    The host secret never leaves /etc/usbypass/secret.key.")
    print("    A udev rule fires usbypass-udev-handler on every USB add/remove,")
    print("    which verifies the handshake and writes /run/usbypass/state.json.")
    print("    PAM's pam_exec module reads that state file in microseconds.")
    print()
    print(ui.bold("  Threat model — read this!"))
    print(f"    {ui.WARN(ui.GLYPH_WARN + ' Anyone with the USB can sudo as the enrolled user.')}")
    print(f"    {ui.WARN(ui.GLYPH_WARN + ' Not a replacement for disk encryption.')}")
    print(f"    {ui.WARN(ui.GLYPH_WARN + ' Not 2FA — collapses auth to one factor while plugged in.')}")
    print(f"    {ui.WARN(ui.GLYPH_WARN + ' Anti-casual-cloning, not anti-tamper-resistant.')}")
    print()
    print(ui.bold("  CLI commands you can run directly:"))
    for cmd, desc in (
        ("usbypass",                 "this interactive menu"),
        ("usbypass status",          "snapshot of state, enrolled keys, attached USBs"),
        ("usbypass list",            "list all enrolled keys"),
        ("sudo usbypass enroll",     "enroll a USB (interactive picker)"),
        ("sudo usbypass verify-now", "force-verify currently attached enrolled USBs"),
        ("sudo usbypass revoke ID",  "revoke an enrolled key by serial or label"),
        ("sudo usbypass doctor",     "sanity-check the installation"),
        ("sudo usbypass install",    "install or repair the PAM hook"),
        ("sudo usbypass uninstall",  "remove the PAM hook"),
    ):
        print(f"    {ui.KEY(cmd):<48}  {ui.dim(desc)}")
    print()
    print(ui.bold("  Files"))
    print(ui.kv("secret",     "/etc/usbypass/secret.key  (root-only HMAC key)"))
    print(ui.kv("registry",   "/var/lib/usbypass/enrolled.json"))
    print(ui.kv("state",      "/run/usbypass/state.json"))
    print(ui.kv("udev rule",  "/etc/udev/rules.d/99-usbypass.rules"))
    print(ui.kv("PAM helper", "/usr/local/libexec/usbypass-pam-helper"))
    print()
    _press_enter()


def _print_enrolled_keys() -> None:
    reg = enrollment.load_registry()
    print()
    print(ui.section("enrolled keys"))
    print()
    if not reg:
        print(f"  {ui.WARN('No keys enrolled.')}")
        print(f"  {ui.MUTED('Run `sudo usbypass enroll` or pick a USB from the main menu.')}")
        print()
        return
    for user, entries in sorted(reg.items()):
        print(f"  {ui.bold(user)}")
        for e in entries:
            ts = e.get("enrolled_at")
            when = ui.fmt_relative(ts) if isinstance(ts, (int, float)) else "?"
            print(
                f"    {ui.GLYPH_KEY} {ui.bold(str(e.get('label')))}"
                f"  {ui.dim('serial=')}{e.get('serial')}"
                f"  {ui.dim('enrolled')} {when}"
            )
        print()


def _show_status_snapshot() -> None:
    """Re-use the CLI status command for parity."""
    import argparse as _a
    from usbypass.cli import _cmd_status
    print()
    _cmd_status(_a.Namespace())
    print()


def _show_doctor() -> None:
    import argparse as _a
    from usbypass.cli import _cmd_doctor
    print()
    _cmd_doctor(_a.Namespace())
    print()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run() -> int:
    if not sys.stdin.isatty():
        print(
            "error: `usbypass` interactive mode requires a TTY. "
            "Pass a subcommand (enroll/list/status/doctor/...) or run `usbypass --help`.",
            file=sys.stderr,
        )
        return 2

    while True:
        ui.clear_screen()
        _print_header()
        _print_state_banner()
        rows = _gather_rows()
        _print_usb_table(rows)
        _print_actions(have_devices=bool(rows))

        raw = _prompt(f"  {ui.ACCENT(ui.GLYPH_ARROW)} ").lower()
        if raw in {"q", "quit", "exit"}:
            return 0
        if raw in {"r", "refresh", ""}:
            continue
        if raw in {"l", "list"}:
            _print_enrolled_keys()
            _press_enter()
            continue
        if raw in {"s", "status"}:
            _show_status_snapshot()
            _press_enter()
            continue
        if raw in {"d", "doctor"}:
            _show_doctor()
            _press_enter()
            continue
        if raw in {"h", "help", "?"}:
            _print_help()
            continue
        if raw in {"e", "enroll"}:
            if not rows:
                print(f"  {ui.WARN('No USB devices to enroll. Plug one in first.')}")
                _press_enter()
                continue
            picked = _pick_index(rows)
            if picked is not None:
                _per_usb_menu(picked)
            continue
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(rows):
                _per_usb_menu(rows[idx - 1])
                continue
        print(f"  {ui.ERR('unknown input: ' + repr(raw))}")
        _press_enter()


def _pick_index(rows: list[UsbRow]) -> UsbRow | None:
    if len(rows) == 1:
        return rows[0]
    raw = _prompt(f"  Pick a device 1-{len(rows)} ({ui.dim('blank to cancel')}): ")
    if not raw:
        return None
    if raw.isdigit() and 1 <= int(raw) <= len(rows):
        return rows[int(raw) - 1]
    print(f"  {ui.ERR('invalid selection')}")
    return None
