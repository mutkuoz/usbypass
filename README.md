# USBYPASS

> Use a physical USB drive as a password-bypass key for Linux `sudo` and
> login, with standard password entry always available as a fallback.

USBYPASS turns any USB mass-storage device into an authentication token
wired into PAM. When the key is plugged in, `sudo` and login skip the
password prompt. When it's absent, the system behaves like a perfectly
ordinary, password-protected Linux box. Anti-clone protection binds the
stored handshake to the physical USB controller's serial number, so
`dd`-ing the drive to another stick will not produce a working clone.

```
+---------+      udev add       +-----------------+       write        +----------------+
|  USB in | ------------------> |  udev handler   | -----------------> |  /run/usbypass |
+---------+                     | verify HMAC     |    state.json      |   /state.json  |
                                +-----------------+                    +----------------+
                                                                              |
                                                                              v
+---------+    sudo / login     +-----------------+      read + HMAC    +----------------+
|  user   | ------------------> |  pam_exec       | <------------------ |  PAM helper    |
+---------+                     |  usbypass       |                     |  (verify.py)   |
                                +-----------------+                     +----------------+
                                        |
                                        | success=done  -> skip password
                                        | default=ignore -> fall through to pam_unix
                                        v
                               +--------------------+
                               |  pam_unix (password)|
                               +--------------------+
```

- **Modular Python 3.9+** package, installable via `install.sh`.
- **Dual-mode auth**: password always works; USB only bypasses.
- **Anti-cloning**: USB controller serial is part of the HMAC input.
- **Cross-distro**: `pam-auth-update` on Debian/Ubuntu; direct PAM
  edits bracketed by markers on Fedora/Arch/other.
- **Hot-unplug lock**: udev triggers a systemd oneshot that wipes
  cached sudo credentials the moment the USB is removed.
- **No login hang**: PAM never scans for devices; it reads a tiny
  state file maintained by the udev handler.

## Table of Contents

- [How it works](#how-it-works)
- [Security warnings](#security-warnings)
- [Installation](#installation)
- [Enrollment](#enrollment)
- [Everyday use](#everyday-use)
- [Uninstallation](#uninstallation)
- [Further reading](#further-reading)

## How it works

1. **Secret generation.** `install.sh` creates `/etc/usbypass/secret.key`
   — 64 random bytes, root-only, mode `0600`. This secret never leaves
   the host.

2. **Enrollment.** `sudo usbypass enroll` reads the USB controller's
   hardware serial via pyudev, computes
   `HMAC-SHA256(secret, "<username>:<serial>")`, and writes it to
   `<USB-mount>/.usbypass/handshake`. The `(username, serial)` pair is
   recorded in `/var/lib/usbypass/enrolled.json`.

3. **Insert / verify.** A udev rule routes every USB block-partition
   `add` event to `/usr/local/libexec/usbypass-udev-handler`. The
   handler looks up the live serial, re-computes the HMAC against the
   host secret, and — only on match — writes
   `/run/usbypass/state.json` recording the verified user.

4. **Authentication.** PAM is configured with:

       auth  [success=done default=ignore]  pam_exec.so quiet /usr/local/libexec/usbypass-pam-helper

   The helper reads `state.json`, re-verifies the HMAC against the
   still-plugged-in device, and exits `0` on success. PAM's control
   expression means a `0` exit short-circuits auth, while any other
   result falls through silently to `pam_unix.so` and the ordinary
   password prompt.

5. **Remove / lock.** The udev `remove` event clears `state.json` and
   fires the `usbypass-clear-sudo.service` systemd oneshot, which
   deletes sudo's credential timestamps under `/run/sudo/ts/`. The
   next `sudo` invocation will re-authenticate against `pam_unix`.

Anti-clone: a duplicated filesystem on a different stick will fail
verification because its controller serial — and therefore the HMAC
input — is different. The host secret required to produce a valid
handshake never leaves `/etc/usbypass/secret.key`.

See [`docs/architecture.md`](docs/architecture.md) for the full state
machine.

## Security warnings

**Read this before installing.** USBYPASS is a convenience layer over
PAM. It weakens an authentication boundary, and you need to understand
what it does and doesn't protect against.

1. **The USB stick is a physical key.** Anyone who physically possesses
   an enrolled USB drive can unlock `sudo` and log in as the enrolled
   user. Treat it like a house key — keep it on your person, not taped
   to the laptop lid.

2. **This is not a replacement for disk encryption.** USBYPASS runs
   *after* the kernel is up and PAM is running, so it cannot protect
   data at rest. Combine it with LUKS / dm-crypt for real confidentiality.

3. **It is not 2FA.** When the key is plugged in, authentication
   collapses from "something you know" (password) to "something you
   have" (USB). For true multi-factor authentication, use a YubiKey
   with PAM-U2F instead.

4. **USB serials are not cryptographically attested.** The scheme
   defeats casual cloning (`dd if=/dev/sdX of=/dev/sdY`), but a
   motivated attacker with the right USB controller firmware could
   forge a serial. USBYPASS is *anti-casual-copying*, not
   *anti-tamper-resistant*.

5. **`pam_exec` runs as root.** Our helper script is deliberately tiny
   (~30 lines on the hot path) and audited. Anyone with write access
   to `/usr/local/libexec/usbypass-pam-helper` already has root, so
   there is no new privilege boundary — but the installer sets `0755
   root:root` on the file and you should monitor it.

6. **Fedora's `authselect` will stomp direct PAM edits.** Running
   `authselect select ...` after installing USBYPASS will overwrite
   `/etc/pam.d/sudo` and `/etc/pam.d/system-auth` from templates. Re-run
   `sudo usbypass install` to restore the hook. On Debian/Ubuntu this is
   a non-issue because we use `pam-auth-update`.

7. **Not compatible with early-boot passphrase prompts.** USBYPASS only
   affects post-boot PAM (login, sudo, gdm, lightdm, xscreensaver).
   LUKS unlock at boot is untouched.

8. **Weak / absent USB serials are refused.** `sudo usbypass enroll`
   will error out on drives that report a blank or obviously-default
   serial (e.g. `0000000000`). You can override this with
   `--force-weak-serial`, but you are then trivially cloneable.

9. **Read [`docs/security.md`](docs/security.md)** for the full threat
   model.

## Installation

Supported distributions:

- Ubuntu / Debian / Linux Mint / Pop!_OS (`pam-auth-update`)
- Fedora / RHEL / Rocky / AlmaLinux (direct PAM edit; see note on
  `authselect` above)
- Arch / Manjaro (direct PAM edit)
- Other Linux with PAM (best-effort direct edit of `/etc/pam.d/sudo`)

### Prerequisites

- Linux 4.15+ with udev
- `python3` 3.9 or newer
- `pyudev` 0.24+ (the installer will pull this via your package manager
  and fall back to `pip` if unavailable)
- Root privileges

### Install

```bash
git clone https://github.com/mutkuoz/usbypass.git
cd usbypass
sudo ./install.sh
```

The installer will:

1. Detect your distro family.
2. Install `pyudev` via `apt`/`dnf`/`pacman` (falling back to `pip`).
3. Copy the Python package to `/opt/usbypass/usbypass/` and drop a
   `.pth` file so `python3 -m usbypass` resolves it.
4. Install shim scripts: `/usr/local/bin/usbypass`,
   `/usr/local/libexec/usbypass-pam-helper`,
   `/usr/local/libexec/usbypass-udev-handler`.
5. Install the udev rule and systemd hot-unplug unit.
6. Generate the host secret at `/etc/usbypass/secret.key`.
7. Install the PAM hook (`pam-auth-update` or direct-edit with
   `.usbypass.bak` backups).

### Verify the install

```bash
sudo usbypass doctor
```

Every check should print `[OK]`. See
[`docs/troubleshooting.md`](docs/troubleshooting.md) if not.

## Enrollment

Plug in a USB drive and make sure it's mounted (your desktop file
manager will normally do this; otherwise run
`udisksctl mount -b /dev/sdX1`). Then:

```bash
sudo usbypass enroll --user "$USER"
```

USBYPASS will print the resolved serial, write the handshake to
`<USB>/.usbypass/handshake`, and record the enrollment. Re-plug the
drive and confirm:

```bash
usbypass status
```

You should see a verified state entry and the USB listed as
`[enrolled]`.

Enroll multiple USB keys per user by re-running the command with
different devices and `--label`s:

```bash
sudo usbypass enroll --user "$USER" --device /dev/sdb1 --label primary
sudo usbypass enroll --user "$USER" --device /dev/sdc1 --label backup
```

Revoke a key:

```bash
sudo usbypass revoke backup --user "$USER"
```

See [`docs/enrollment.md`](docs/enrollment.md) for detailed walkthroughs.

## Everyday use

| Scenario                                 | Behaviour                        |
|------------------------------------------|----------------------------------|
| USB plugged in, enrolled, verified       | `sudo` / login skip the password |
| USB plugged in, wrong serial (clone)     | Normal password prompt           |
| USB plugged in, handshake tampered       | Normal password prompt           |
| USB removed mid-session                  | Cached sudo credentials cleared; next `sudo` requires password |
| USB not plugged in                       | Normal password prompt           |
| Rescue / single-user / serial console    | Normal password prompt           |

At any time you can run `usbypass status` to see what USBYPASS thinks
the world looks like.

## Uninstallation

```bash
sudo ./uninstall.sh            # keeps /etc/usbypass and /var/lib/usbypass
sudo ./uninstall.sh --purge    # also removes secret + enrollment registry
```

On Fedora/Arch, the direct PAM edits are removed by searching for our
marker comments; the `.usbypass.bak` files left by `install.sh` can be
restored manually if anything goes wrong. On Debian/Ubuntu we just
delete `/usr/share/pam-configs/usbypass` and re-run
`pam-auth-update --package`.

## Further reading

- [`docs/architecture.md`](docs/architecture.md) — components, data
  flow, state machine.
- [`docs/pam-stack.md`](docs/pam-stack.md) — exact PAM edits per
  distro, before/after diffs.
- [`docs/security.md`](docs/security.md) — threat model, limitations,
  future work.
- [`docs/enrollment.md`](docs/enrollment.md) — step-by-step enrollment.
- [`docs/troubleshooting.md`](docs/troubleshooting.md) — recovery
  recipes if you lock yourself out.

## License

MIT. See [`LICENSE`](LICENSE).
