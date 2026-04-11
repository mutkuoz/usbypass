# Architecture

This document explains the components of USBYPASS, how they interact,
and why the fast-path design avoids the "scanning for USB at login"
hang common to simpler approaches.

## Components

| Component                          | Path                                                    | Runs as            | Triggered by                     |
|------------------------------------|---------------------------------------------------------|--------------------|----------------------------------|
| `usbypass` CLI                     | `/usr/local/bin/usbypass`                               | user / root        | user invocation                  |
| PAM helper                         | `/usr/local/libexec/usbypass-pam-helper`                | root (pam_exec)    | PAM `auth` phase                 |
| udev handler                       | `/usr/local/libexec/usbypass-udev-handler`              | root (udev worker) | udev `add`/`remove`/`change`     |
| Sudo-clear systemd unit            | `usbypass-clear-sudo.service`                           | root               | udev `remove` (via `systemctl`)  |
| Host secret                        | `/etc/usbypass/secret.key`                              | root-only          | created by installer             |
| Enrollment registry                | `/var/lib/usbypass/enrolled.json`                       | root               | `usbypass enroll`                |
| Verified state file                | `/run/usbypass/state.json`                              | root               | udev handler                     |

## Data flow — insertion

```
USB inserted
     |
     v
udev event (add, sdXN, ID_BUS=usb)
     |
     v
/usr/local/libexec/usbypass-udev-handler add sdXN
     |
     +-- serial = pyudev ID_SERIAL_SHORT(sdXN)
     +-- lookup enrolled.json -> candidate usernames?
     |      no  -> exit 0 (foreign drive, ignore)
     |      yes -> wait_for_mount(sdXN, timeout=500ms)
     |                not mounted -> exit 0 (log)
     |                mounted     -> read .usbypass/handshake
     |                                 missing -> exit 0 (log)
     |                                 present -> HMAC verify
     |                                               fail -> exit 0 (log 'possible clone')
     |                                               pass -> write state.json
     v
exit 0 (fast; udev worker unblocked)
```

## Data flow — authentication (PAM hot path)

```
sudo / login / gdm / xscreensaver
     |
     v
PAM auth stack
     |
     v
pam_exec.so -> usbypass-pam-helper
     |
     v
read /run/usbypass/state.json
     |
     +-- missing        -> exit 1 (fall through)
     +-- user mismatch  -> exit 1
     +-- live serial lookup via /proc/self/mountinfo + /sys
     |      mount gone  -> exit 1
     |      HMAC mismatch (tampered file) -> exit 1
     +-- all good       -> exit 0 (PAM success=done)
     v
PAM falls back to pam_unix.so (password prompt) on any non-zero
```

The helper does **not** import pyudev. It does **not** enumerate
devices. All it needs on the hot path is:

1. One `json.load` of a 200-byte file (cached in the kernel page
   cache — measured at ~2-5 ms cold, sub-millisecond warm).
2. One scan of `/proc/self/mountinfo` (which the kernel already reads
   from memory).
3. One `os.readlink`-chain under `/sys/block/` to get the serial.
4. One `hmac.compare_digest`.

Total measured overhead on a mid-range laptop: **under 30 ms**.

## Data flow — removal

```
USB removed
     |
     v
udev event (remove, sdXN)
     |
     v
/usr/local/libexec/usbypass-udev-handler remove sdXN
     |
     +-- state.json exists and devnode matches?
     |      no   -> exit 0
     |      yes  -> clear state.json
     |              systemctl start --no-block usbypass-clear-sudo.service
     |              also directly unlink /run/sudo/ts/* as a fallback
     v
exit 0
```

The sudo-credential clear is belt-and-braces: even if the systemd
oneshot never runs (e.g. during shutdown), the next `sudo` call will
invoke our PAM helper, find `state.json` gone, and fall through to
`pam_unix` which prompts for a password.

## State machine

```
              +---------------+
              |  NO KEY       |
              | (state.json   |
              |  absent)      |
              +-------+-------+
                      | udev add + verify OK
                      v
              +---------------+
              |  VERIFIED     |
              | (state.json   |
              |  present)     |
              +-------+-------+
                      | udev remove
                      v
              +---------------+
              |  NO KEY       |
              +---------------+
```

There is intentionally no "INVALID / FAILED" state written to disk.
Failed verifications are logged and the handler exits without touching
the state file. This keeps the PAM helper's logic trivial: presence
implies trust (plus a defense-in-depth re-verification).

## File and directory layout

```
/etc/usbypass/
    secret.key              # 64 random bytes, 0600
/var/lib/usbypass/
    enrolled.json           # {username: [{serial, label, enrolled_at}, ...]}
/run/usbypass/              # tmpfs; cleared on reboot
    state.json              # active verified-state (0600)
/opt/usbypass/
    usbypass/               # Python package
/usr/local/bin/
    usbypass                # CLI shim
/usr/local/libexec/
    usbypass-pam-helper     # called by pam_exec
    usbypass-udev-handler   # called by udev RUN
/etc/udev/rules.d/
    99-usbypass.rules
/etc/systemd/system/
    usbypass-clear-sudo.service
```

## Why not a long-running daemon?

Earlier drafts had a `usbypassd` monitoring pyudev events from a
systemd service. It was dropped because:

- udev's own RUN hook already gives us synchronous, ordered events.
- Adding a daemon means another thing to keep alive, monitor, and
  supervise for a rare event (seconds between plug/unplug).
- The PAM hot path never needed the daemon — it only reads
  `state.json` — so the daemon would exist solely to write one file.

A short-lived udev worker is strictly simpler and not meaningfully
slower.

## Module map

```
usbypass/
  __init__.py        # version
  __main__.py        # python -m usbypass entry
  cli.py             # argparse dispatcher, subcommands
  config.py          # paths & constants (single source of truth)
  crypto.py          # HMAC core + secret I/O
  state.py           # /run/usbypass/state.json read/write
  usb.py             # pyudev enumeration + fast-path /proc/mountinfo + /sys
  enrollment.py      # enrolled.json load/save/add/remove
  enroll.py          # interactive enrollment (uses enrollment.py)
  verify.py          # PAM hot path
  handler.py         # udev add/remove handler + sudo-ts clearing
  pam_installer.py   # PAM stack mutation (debian + direct-edit)
  logger.py          # syslog + stderr helper
```

Hot-path imports (reachable from `verify.py`):

```
verify.py
 -> crypto.py
     -> hashlib, hmac (stdlib)
 -> state.py
     -> json, os (stdlib)
 -> usb.py         # only the fast-path functions are used
     -> os, time, pathlib (stdlib)
```

No pyudev, no logging, no subprocess on the hot path.
