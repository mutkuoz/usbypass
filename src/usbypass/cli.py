"""USBYPASS command-line interface.

Subcommands:

  enroll      Register a USB drive as a bypass key.
  list        Show enrolled keys.
  revoke      Remove an enrolled key.
  status      Show current verified-state and present USBs.
  doctor      Sanity-check installation.
  install     Install PAM hook (usually run by install.sh).
  uninstall   Remove PAM hook.
  verify      Internal: called by pam_exec.
  handle-udev Internal: called by udev RUN rule.
  clear-sudo  Internal: called by systemd hot-unplug unit.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from usbypass import __version__


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="usbypass",
        description=(
            "Use a physical USB drive as a password-bypass key for Linux PAM.\n"
            "Run with no subcommand for the interactive menu."
        ),
    )
    p.add_argument("--version", action="version", version=f"usbypass {__version__}")
    sub = p.add_subparsers(dest="cmd", required=False)

    # enroll
    s = sub.add_parser("enroll", help="Register a USB drive as a bypass key")
    s.add_argument("--user", "-u", default=None, help="Target username (default: $SUDO_USER)")
    s.add_argument("--device", "-d", default=None, help="/dev/sdXN to enroll")
    s.add_argument("--label", "-l", default=None, help="Friendly label for the key")
    s.add_argument(
        "--force-weak-serial",
        action="store_true",
        help="Enroll a drive even if its serial is weak/default (UNSAFE)",
    )

    # list
    sub.add_parser("list", help="List enrolled keys")

    # revoke
    s = sub.add_parser("revoke", help="Revoke an enrolled key")
    s.add_argument("identifier", help="Serial or label of the key")
    s.add_argument("--user", "-u", default=None, help="Target username (default: $SUDO_USER)")

    # status
    sub.add_parser("status", help="Show current USBYPASS state")

    # verify-now (manual re-run of the udev handler)
    s = sub.add_parser(
        "verify-now",
        help="Force-verify currently attached enrolled USBs (writes state.json)",
    )
    s.add_argument(
        "--device",
        "-d",
        default=None,
        help="Only verify this /dev/sdXN (default: all enrolled USBs present)",
    )

    # doctor
    sub.add_parser("doctor", help="Run installation sanity checks")

    # install / uninstall
    s = sub.add_parser("install", help="Install the PAM hook")
    s.add_argument("--pam-only", action="store_true", help="Only touch the PAM stack")

    sub.add_parser("uninstall", help="Uninstall the PAM hook")

    # Internal subcommands
    sub.add_parser("verify", help=argparse.SUPPRESS)

    s = sub.add_parser("handle-udev", help=argparse.SUPPRESS)
    s.add_argument("action")
    s.add_argument("kernel_name")

    sub.add_parser("clear-sudo", help=argparse.SUPPRESS)

    return p


def _resolve_user(explicit: str | None) -> str:
    if explicit:
        return explicit
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        return sudo_user
    return os.environ.get("USER", "root")


def _cmd_enroll(args: argparse.Namespace) -> int:
    from usbypass import ui
    from usbypass.enroll import EnrollError, enroll

    username = _resolve_user(args.user)
    try:
        result = enroll(
            username=username,
            device=args.device,
            label=args.label,
            allow_weak_serial=args.force_weak_serial,
        )
    except EnrollError as exc:
        print(f"{ui.ERR('error:')} {exc}", file=sys.stderr)
        return 1
    print()
    print(f"  {ui.OK(ui.GLYPH_CHECK + ' USB key enrolled')}")
    for k, v in result.items():
        print(ui.kv(k, str(v)))
    print()
    print(ui.bold("  Next steps"))
    print(f"    1. {ui.dim('Unplug and re-plug the USB (or run)')} {ui.KEY('sudo usbypass verify-now')}")
    print(f"    2. {ui.dim('Check')} {ui.KEY('usbypass status')}{ui.dim(' — it should show the key as VERIFIED.')}")
    print(f"    3. {ui.dim('Try')} {ui.KEY('sudo -k && sudo echo ok')}{ui.dim(' — no password expected.')}")
    print()
    return 0


def _cmd_list(_: argparse.Namespace) -> int:
    from usbypass import enrollment, ui

    registry = enrollment.load_registry()
    print()
    print(ui.section("enrolled keys"))
    print()
    if not registry:
        print(f"  {ui.WARN('No USB keys enrolled.')}")
        print(f"  {ui.MUTED('Run `sudo usbypass enroll` to add one.')}")
        print()
        return 0
    for user, entries in sorted(registry.items()):
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
    return 0


def _cmd_revoke(args: argparse.Namespace) -> int:
    from usbypass import enrollment

    username = _resolve_user(args.user)
    if enrollment.remove_entry(username, args.identifier):
        print(f"Revoked key {args.identifier!r} for user {username!r}.")
        return 0
    print(f"No matching key {args.identifier!r} enrolled for {username!r}.", file=sys.stderr)
    return 1


def _cmd_status(_: argparse.Namespace) -> int:
    from usbypass import enrollment, state, ui, usb

    width = ui.term_width()
    print()
    print(ui.box_top("USBYPASS status", width))
    print(ui.box_bottom(width))
    print()

    st = state.read_state()
    print(ui.bold("  Active key"))
    if st is None:
        if os.geteuid() != 0 and state.state_file_path().exists():
            print(f"    {ui.WARN('state file exists but is not readable by this user')}")
            print(f"    {ui.MUTED('re-run as:')} {ui.KEY('sudo usbypass status')}")
        else:
            print(f"    {ui.MUTED(ui.GLYPH_DOT_OFF + ' no active USB key — password auth in effect')}")
    else:
        ts = st.get("verified_at")
        when = ui.fmt_relative(ts) if isinstance(ts, (int, float)) else "?"
        when_abs = ui.fmt_absolute(ts) if isinstance(ts, (int, float)) else "?"
        print(f"    {ui.OK(ui.GLYPH_DOT_ON + ' ' + str(st.get('username') or '?'))}"
              f"  {ui.dim('via')} {st.get('devnode') or '?'}")
        print(ui.kv("serial",    str(st.get("serial") or "?")))
        print(ui.kv("verified",  f"{when}  {ui.dim('(' + when_abs + ')')}"))
        print(ui.kv("state file", str(state.state_file_path())))
    print()

    registry = enrollment.load_registry()
    print(ui.bold("  Enrolled keys"))
    if not registry:
        print(f"    {ui.MUTED('(none)')}")
    else:
        for user, entries in sorted(registry.items()):
            for e in entries:
                ts = e.get("enrolled_at")
                when = ui.fmt_relative(ts) if isinstance(ts, (int, float)) else "?"
                print(
                    f"    {ui.GLYPH_KEY} {ui.bold(user)}  "
                    f"{ui.bold(str(e.get('label')))}"
                    f"  {ui.dim('[' + str(e.get('serial')) + ']')}"
                    f"  {ui.dim(when)}"
                )
    print()

    parts = usb.list_usb_partitions_safe()
    print(ui.bold("  Currently attached USB partitions"))
    if not parts:
        print(f"    {ui.MUTED('(none)')}")
    else:
        verified_serial = (st or {}).get("serial")
        for p in parts:
            enrolled_users = [
                user for user, entries in registry.items()
                if any(e.get("serial") == p.serial for e in entries)
            ]
            tags: list[str] = []
            if enrolled_users:
                tags.append(ui.INFO(f"enrolled:{','.join(enrolled_users)}"))
            if verified_serial and verified_serial == p.serial:
                tags.append(ui.OK(f"{ui.GLYPH_CHECK} VERIFIED"))
            tag_str = "  ".join(tags)
            mp = str(p.mountpoint) if p.mountpoint else ui.MUTED("‹not mounted›")
            size = ui.fmt_bytes(p.size_bytes)
            fs = p.fs_type or "?"
            desc = f"{(p.vendor or '').strip()} {(p.model or '').strip()}".strip()
            print(
                f"    {ui.bold(p.devnode)}  {desc}  "
                f"{ui.dim('(' + size + ' · ' + fs + ')')}"
            )
            print(
                f"       {ui.dim('serial=')}{p.serial or '?'}"
                f"  {ui.dim('mount=')}{mp}"
            )
            if tag_str:
                print(f"       {tag_str}")
    print()

    # Helpful nudge: an enrolled USB is attached but not verified.
    enrolled_present_not_verified = [
        p for p in parts
        if any(
            e.get("serial") == p.serial
            for entries in registry.values()
            for e in entries
        )
        and (not st or st.get("serial") != p.serial)
    ]
    if enrolled_present_not_verified:
        print(
            f"  {ui.WARN(ui.GLYPH_WARN + ' Hint:')} {ui.dim('enrolled USB detected but not active.')}"
        )
        print(f"    {ui.dim('Run')} {ui.KEY('sudo usbypass verify-now')} {ui.dim('to force verification.')}")
        print()
    return 0


def _cmd_verify_now(args: argparse.Namespace) -> int:
    from usbypass import crypto, enrollment, state, usb

    if os.geteuid() != 0:
        print("error: verify-now must run as root (it reads the host secret)", file=sys.stderr)
        return 1

    parts = usb.list_usb_partitions_safe()
    if args.device:
        parts = [p for p in parts if p.devnode == args.device]
    if not parts:
        print("No matching USB partitions are attached.", file=sys.stderr)
        return 1

    registry = enrollment.load_registry()
    any_verified = False
    for p in parts:
        if not p.serial:
            continue
        matches = list(usb.iter_enrolled_matches(p.serial, registry))
        if not matches:
            continue
        stored = usb.read_handshake_any(p.devnode, p.mountpoint)
        if stored is None:
            print(f"  {p.devnode}: could not read handshake (mount + temp-mount both failed)")
            continue
        verified_here = False
        for user in matches:
            if crypto.verify_handshake(user, p.serial, stored):
                state.write_state(username=user, serial=p.serial, devnode=p.devnode)
                print(f"  {p.devnode}: verified as user={user} serial={p.serial}")
                any_verified = True
                verified_here = True
                break
        if not verified_here:
            print(f"  {p.devnode}: HMAC mismatch — handshake does not match host secret")
    if not any_verified:
        print("No enrolled USB could be verified.", file=sys.stderr)
        return 1
    return 0


def _cmd_doctor(_: argparse.Namespace) -> int:
    from usbypass import config, crypto, ui
    from usbypass.pam_installer import detect_family

    ok = True

    def check(label: str, cond: bool, hint: str = "") -> None:
        nonlocal ok
        if cond:
            print(f"    {ui.OK(ui.GLYPH_CHECK)}  {label}")
        else:
            ok = False
            line = f"    {ui.ERR(ui.GLYPH_CROSS)}  {label}"
            if hint:
                line += f"  {ui.dim('— ' + hint)}"
            print(line)

    width = ui.term_width()
    print()
    print(ui.box_top("USBYPASS doctor", width))
    print(ui.box_bottom(width))
    print()
    print(ui.bold("  Installation checks"))
    # /etc/usbypass is mode 0700, so non-root can't even stat the file.
    # Treat that as "unknown" rather than failing the check.
    if os.geteuid() != 0:
        print(f"    {ui.WARN(ui.GLYPH_WARN)}  Host secret check requires root  {ui.dim('— re-run with sudo')}")
    else:
        check(
            f"Host secret present at {config.SECRET_PATH}",
            config.SECRET_PATH.exists(),
            "run install.sh or `sudo usbypass install`",
        )
        try:
            crypto.load_secret()
            check("Secret readable and permissions OK", True)
        except crypto.SecretMissingError as exc:
            check("Secret readable and permissions OK", False, str(exc))
        except PermissionError as exc:
            check("Secret readable and permissions OK", False, str(exc))

    helper = Path("/usr/local/libexec/usbypass-pam-helper")
    check(f"PAM helper at {helper}", helper.exists())

    udev_rule = Path("/etc/udev/rules.d/99-usbypass.rules")
    check(f"udev rule at {udev_rule}", udev_rule.exists())

    cli = Path("/usr/local/bin/usbypass")
    check(f"CLI shim at {cli}", cli.exists() or cli.is_symlink())

    systemd_unit = Path("/etc/systemd/system/usbypass-clear-sudo.service")
    check(f"systemd unit at {systemd_unit}", systemd_unit.exists())

    # pyudev optional but strongly recommended
    try:
        import pyudev  # noqa: F401
        check("pyudev importable", True)
    except Exception as exc:
        check("pyudev importable", False, str(exc))

    print()
    print(ui.bold("  Environment"))
    print(ui.kv("distro family", detect_family()))
    print(ui.kv("running as",    "root" if os.geteuid() == 0 else "user (" + (os.environ.get('USER') or '?') + ')'))
    print()
    if ok:
        print(f"  {ui.OK(ui.GLYPH_CHECK + ' everything looks healthy')}")
    else:
        print(f"  {ui.ERR(ui.GLYPH_CROSS + ' some checks failed — see above')}")
    print()
    return 0 if ok else 1


def _cmd_install(args: argparse.Namespace) -> int:
    from usbypass import crypto, pam_installer

    if os.geteuid() != 0:
        print("error: install must run as root", file=sys.stderr)
        return 1
    if not args.pam_only:
        crypto.generate_secret()
    try:
        family = pam_installer.install()
    except Exception as exc:
        print(f"PAM install failed: {exc}", file=sys.stderr)
        return 1
    print(f"USBYPASS PAM hook installed ({family}).")
    return 0


def _cmd_uninstall(_: argparse.Namespace) -> int:
    from usbypass import pam_installer

    if os.geteuid() != 0:
        print("error: uninstall must run as root", file=sys.stderr)
        return 1
    try:
        family = pam_installer.uninstall()
    except Exception as exc:
        print(f"PAM uninstall failed: {exc}", file=sys.stderr)
        return 1
    print(f"USBYPASS PAM hook removed ({family}).")
    return 0


def _cmd_verify(_: argparse.Namespace) -> int:
    from usbypass.verify import main as verify_main

    return verify_main()


def _cmd_handle_udev(args: argparse.Namespace) -> int:
    from usbypass.handler import main as handler_main

    return handler_main([args.action, args.kernel_name])


def _cmd_clear_sudo(_: argparse.Namespace) -> int:
    from usbypass.handler import clear_sudo_timestamps

    clear_sudo_timestamps()
    return 0


_DISPATCH = {
    "enroll": _cmd_enroll,
    "list": _cmd_list,
    "revoke": _cmd_revoke,
    "status": _cmd_status,
    "verify-now": _cmd_verify_now,
    "doctor": _cmd_doctor,
    "install": _cmd_install,
    "uninstall": _cmd_uninstall,
    "verify": _cmd_verify,
    "handle-udev": _cmd_handle_udev,
    "clear-sudo": _cmd_clear_sudo,
}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        # No subcommand → interactive mode.
        from usbypass.interactive import run as interactive_run
        try:
            return interactive_run()
        except KeyboardInterrupt:
            print()
            return 130
    return _DISPATCH[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
