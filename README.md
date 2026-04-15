# USBYPASS
<img width="1024" height="559" alt="image" src="https://github.com/user-attachments/assets/91dfbdb5-f7f7-4f00-99da-61f9439980c3" />

> Use a physical USB drive as a password-bypass key for Linux `sudo` and
> login, with standard password entry always available as a fallback.

USBYPASS turns any USB mass-storage device into an authentication token
wired into PAM. When the enrolled key is plugged in, `sudo` and login
skip the password prompt. When it's absent, the system behaves like a
perfectly ordinary, password-protected Linux box. Anti-clone protection
binds the stored handshake to the physical USB controller's serial
number, so `dd`-ing the drive to another stick will **not** produce a
working clone.

```text
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
                                        | success=done   -> skip password
                                        | default=ignore -> fall through to pam_unix
                                        v
                               +---------------------+
                               |  pam_unix (password)|
                               +---------------------+
```

## Features

- **Interactive TUI** — run `usbypass` with no arguments and get a
  colorized, line-oriented menu of attached USBs with per-device
  enroll / verify / revoke actions, no curses required.
- **Modular Python 3.9+** package, installable via `install.sh`, an
  RPM, a `.deb`, or an Arch package.
- **Dual-mode auth**: password always works; USB only bypasses.
- **Anti-cloning**: USB controller serial is part of the HMAC input.
- **Headless-friendly**: handler privately temp-mounts the USB if no
  desktop autonomous-mounter is running, so verification works on
  servers and rescue shells.
- **Cross-distro**: `pam-auth-update` on Debian/Ubuntu; direct PAM
  edits bracketed by markers on Fedora/Arch/other.
- **Hot-unplug lock**: udev triggers a systemd oneshot that wipes
  cached sudo credentials the moment the USB is removed.
- **Zero-PAM-hang**: PAM never scans for devices; it reads a tiny
  state file maintained by the udev handler in microseconds.
- **`usbypass verify-now`**: force-verify currently attached USBs
  without unplugging — useful when udisks2 mounted the drive after
  the udev handler's window closed.

## Interactive TUI

```text
╭─ USBYPASS · physical USB key for Linux PAM ────────────────────────────────╮
╰────────────────────────────────────────────────────────────────────────────╯

  ● Active key  mutkuoz [9207027C03D92515872] via /dev/sda1
      verified 2 minutes ago  (2026-04-12 00:46:12)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ USB devices ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  [1] ● /dev/sda1   VendorCo ProductCode
      size:  14.7 GB   fs: exfat   label: USB16G
      serial: 9207027C03D92515872
      mount:  /run/media/mutkuoz/USB16G
      ✓ VERIFIED  🔑 enrolled: mutkuoz

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ actions ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  [1-N] select device   [e] enroll wizard   [r] refresh
  [s] status snapshot     [l] list enrolled       [d] doctor
  [h] help                [q] quit

  →
```

Pick a device by number and you land on a per-device detail view with
inline `enroll / verify-now / revoke / unmount` actions and an
anti-clone-strength indicator.

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

USBYPASS ships in three flavors: an upstream `install.sh` that works on
every supported distro, and native packages for Debian-family,
Red-Hat-family, and Arch-family systems.

Supported distributions:

| Family                                              | PAM strategy        | Package |
|-----------------------------------------------------|---------------------|---------|
| Ubuntu / Debian / Linux Mint / Pop!_OS / Elementary | `pam-auth-update`   | `.deb`  |
| Fedora / RHEL / Rocky / AlmaLinux / openSUSE        | direct PAM edit\*   | `.rpm`  |
| Arch / Manjaro / EndeavourOS                        | direct PAM edit     | `pkg.tar.zst` |
| Other Linux with PAM                                | best-effort edit    | `install.sh` |

\* Note on Fedora's `authselect`: running `authselect select ...` will
overwrite `/etc/pam.d/sudo` and `/etc/pam.d/system-auth` from
templates. Re-run `sudo usbypass install` to restore the hook
afterwards. On Debian/Ubuntu this is a non-issue because we use
`pam-auth-update`.

### Prerequisites

- Linux 4.15+ with udev
- `python3` 3.9 or newer
- `pyudev` 0.24+ (pulled in by the package or by `install.sh`)
- Root privileges
- An exfat / vfat / ntfs / ext4 USB drive (any FS the kernel can mount)

### Option A — Native package (recommended once we publish)

**Debian / Ubuntu (.deb)**

```bash
# from a release artifact
sudo apt install ./usbypass_0.1.0-1_all.deb

# or, once published to a PPA:
sudo add-apt-repository ppa:mutkuoz/usbypass
sudo apt update && sudo apt install usbypass
```

**Fedora / RHEL / Rocky (.rpm)**

```bash
# from a release artifact
sudo dnf install ./usbypass-0.1.0-1.fc43.noarch.rpm

# or, once published to COPR:
sudo dnf copr enable mutkuoz/usbypass
sudo dnf install usbypass
```

**Arch / Manjaro (AUR)**

```bash
# once the AUR package is published:
yay -S usbypass            # or paru / aurman / etc.

# or build by hand from this repo:
cd packaging/arch
makepkg -si
```

The package's `postinst`/`%post`/`post_install` will:

1. Generate the host secret at `/etc/usbypass/secret.key` (root-only).
2. Install the udev rule and reload udev.
3. Install the systemd hot-unplug unit.
4. Wire the PAM hook (via `pam-auth-update` on Debian, marker-bracketed
   direct edit on Fedora/Arch).

### Option B — `install.sh` (any distro)

```bash
git clone https://github.com/mutkuoz/usbypass.git
cd usbypass
sudo ./install.sh
```

The installer will:

1. Detect your distro family.
2. Install `pyudev` via `apt` / `dnf` / `pacman` (falling back to `pip`).
3. Copy the Python package to `/opt/usbypass/usbypass/` and drop a
   `.pth` file so `python3 -m usbypass` resolves it.
4. Install shim scripts: `/usr/local/bin/usbypass`,
   `/usr/local/libexec/usbypass-pam-helper`,
   `/usr/local/libexec/usbypass-udev-handler`.
5. Install the udev rule and systemd hot-unplug unit.
6. Generate the host secret at `/etc/usbypass/secret.key`.
7. Install the PAM hook.

### Option C — Build the packages yourself

```bash
make tarball   # source tarball used by RPM/Arch
make deb       # produces ../usbypass_0.1.0-1_all.deb
make rpm       # produces dist/usbypass-0.1.0-1.*.noarch.rpm
make arch      # produces dist/usbypass-0.1.0-1-any.pkg.tar.zst
```

Each target prints the resulting artifact path and the install
command. See `make help` for the full list.

### Verify the install

```bash
sudo usbypass doctor
```

Every check should print a green ✓. See
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

### CLI quick reference

| Command                          | What it does                                          |
|----------------------------------|-------------------------------------------------------|
| `usbypass`                       | Launch the interactive TUI (no args)                  |
| `usbypass status`                | Snapshot of state, enrolled keys, attached USBs       |
| `usbypass list`                  | List all enrolled keys                                |
| `sudo usbypass enroll`           | Enroll a USB (interactive picker)                     |
| `sudo usbypass enroll -d /dev/sdX1 -u alice -l primary` | Non-interactive |
| `sudo usbypass verify-now`       | Force-verify currently attached enrolled USBs         |
| `sudo usbypass verify-now -d /dev/sdX1` | Verify a single device                         |
| `sudo usbypass revoke ID`        | Revoke a key by serial **or** label                   |
| `sudo usbypass doctor`           | Sanity-check the installation                         |
| `sudo usbypass install`          | Install or repair the PAM hook                        |
| `sudo usbypass uninstall`        | Remove the PAM hook                                   |
| `usbypass --help`                | Full subcommand listing                               |

### Why your USB might not verify automatically

The udev handler runs as root and waits up to 4 seconds for an
auto-mount before falling back to a private read-only temp-mount under
`/run/usbypass/mnt/`. If verification still doesn't happen — most often
because the partition refuses to mount, or because GVFS put the mount
in a per-user namespace invisible to root — run:

```bash
sudo usbypass verify-now
```

That command does the same work the udev handler does, but you control
the timing. After it succeeds, the next `sudo` will skip the prompt as
expected.

At any time you can run `usbypass status` to see what USBYPASS thinks
the world looks like; an enrolled USB attached but not verified will
print a hint pointing at `verify-now`.

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

## Publishing to distro repositories

The repo ships ready-to-build packaging metadata for every major
distro family. Here is the cookbook for getting USBYPASS into each
upstream channel.

### Arch User Repository (AUR) — easiest

The AUR is a community git host: any user can submit a `PKGBUILD` and
it becomes installable via `yay`/`paru`. There is no review queue.

```bash
# 1. Create an AUR account at https://aur.archlinux.org/register
# 2. Add your SSH key to your AUR profile
# 3. Clone the empty package repo:
git clone ssh://aur@aur.archlinux.org/usbypass.git aur-usbypass
cd aur-usbypass

# 4. Drop our PKGBUILD in and generate the metadata file:
cp ../usbypass/packaging/arch/PKGBUILD .
cp ../usbypass/packaging/arch/usbypass.install .
cp ../usbypass/packaging/arch/99-usbypass.rules .
makepkg --printsrcinfo > .SRCINFO

# 5. Push:
git add PKGBUILD .SRCINFO usbypass.install 99-usbypass.rules
git commit -m "usbypass 0.1.0 — initial release"
git push origin master
```

After this, anyone can `yay -S usbypass`. Updates: bump `pkgver` and
re-run `makepkg --printsrcinfo > .SRCINFO`.

### Fedora COPR — easiest for RPM

[Fedora COPR](https://copr.fedorainfracloud.org/) is a free build
service that produces signed RPMs for Fedora, EPEL, RHEL, OpenSUSE,
Mageia, and more. No package-review queue.

```bash
# 1. Create a Fedora account & log into https://copr.fedorainfracloud.org/
# 2. Click "New Project", call it usbypass, pick the chroots you want
#    (e.g. fedora-rawhide, fedora-43, epel-9).
# 3. Click "New Build" → "SCM", and point it at this git repo:
#       Clone URL: https://github.com/mutkuoz/usbypass
#       Spec File: packaging/rpm/usbypass.spec
#       Type:      git
# 4. Wait. COPR builds the source RPM from a checkout, then the binary
#    RPM in mock for every chroot.
# 5. Once green, users can install with:
#       sudo dnf copr enable mutkuoz/usbypass
#       sudo dnf install usbypass
```

You can also drive COPR from the CLI:

```bash
sudo dnf install copr-cli
copr-cli build mutkuoz/usbypass packaging/rpm/usbypass.spec
```

### Ubuntu PPA (Launchpad) — for `.deb`

Personal Package Archives are Launchpad's COPR equivalent. They build
source packages into binaries for every Ubuntu release you target.

```bash
# 1. Create an account at https://launchpad.net/ and add a GPG key.
# 2. Generate a PPA at https://launchpad.net/~YOURUSER/+activate-ppa
# 3. Build a signed source package locally:
sudo apt install devscripts dput debhelper dh-python python3-all
debuild -S -sa
# (this creates ../usbypass_0.1.0-1_source.changes and friends)

# 4. Upload:
dput ppa:YOURUSER/usbypass ../usbypass_0.1.0-1_source.changes

# 5. Wait for Launchpad to email you the build result. Once it's green:
sudo add-apt-repository ppa:YOURUSER/usbypass
sudo apt update && sudo apt install usbypass
```

### openSUSE Build Service (OBS) — multi-distro at once

If you want to publish for openSUSE, Fedora, RHEL, Debian, Ubuntu,
Arch, and more from a single source — OBS is the most powerful option.
Sign up at <https://build.opensuse.org/> and upload either the RPM
spec, the Debian sources, or the Arch PKGBUILD; OBS will rebuild for
every chroot you enable.

### Official distro inclusion (the slow path)

Once the package has been used in the wild for a while:

- **Debian**: file an [ITP bug](https://www.debian.org/devel/wnpp/),
  find a sponsor on `debian-mentors`, upload to NEW.
- **Fedora**: file a [package review request](https://bugzilla.redhat.com/enter_bug.cgi?product=Fedora&component=Package%20Review),
  get a sponsor from the Fedora package-sponsors group.
- **Arch official repos**: package starts in AUR; if it gets adopted
  by a TU it can move into `[community]`.

Both Debian and Fedora package reviews routinely take 2–6 months.

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
