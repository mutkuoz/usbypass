# Enrollment walkthrough

Enrolling a USB drive teaches USBYPASS to trust that specific piece of
hardware as an authentication token for a specific user. You can
enroll multiple drives per user (e.g. a primary and a spare), and a
single drive can only be bound to one user at a time.

## Prerequisites

- USBYPASS installed (`sudo ./install.sh` completed successfully).
- `sudo usbypass doctor` reports all checks green.
- A USB drive you're willing to dedicate to this use (the handshake
  file is small, but the drive is now security-relevant — don't use
  one you hand out for file transfer).

## Quick enroll (single USB, auto-mounted)

```bash
sudo usbypass enroll --user "$USER"
```

If there is exactly one USB partition attached and it's mounted,
USBYPASS will pick it automatically. You'll see something like:

```
USB key enrolled:
  username: alice
  devnode: /dev/sdb1
  serial: 0123456789ABCD
  label: usb-01234567
  handshake_path: /media/alice/MYKEY/.usbypass/handshake

Next steps:
  1. Unplug and re-plug the USB.
  2. Run `usbypass status` — it should show the key as verified.
  3. Open a new shell and try `sudo -k; sudo echo ok` — no password expected.
```

## Enroll with an explicit device

```bash
sudo usbypass enroll --user alice --device /dev/sdc1 --label "work-primary"
```

Use `--device` when you have multiple USBs plugged in, or when running
from a non-TTY context (e.g. inside another script).

## Multiple keys per user

```bash
sudo usbypass enroll --user alice --device /dev/sdb1 --label primary
sudo usbypass enroll --user alice --device /dev/sdc1 --label backup
```

Each (serial) gets its own entry in `/var/lib/usbypass/enrolled.json`.
Plugging in *any* enrolled drive verifies.

## Re-enrolling

Running `usbypass enroll` on a drive that's already enrolled is safe
— the registry entry's timestamp and label are updated in place, and
the handshake file is re-written (useful after regenerating the host
secret).

## Listing and revoking

```bash
usbypass list

alice:
  - serial=0123456789ABCD  label=primary  enrolled_at=1712345678.0
  - serial=FEDCBA9876543210 label=backup   enrolled_at=1712346000.0
```

Revoke by serial or label:

```bash
sudo usbypass revoke backup --user alice
# or
sudo usbypass revoke FEDCBA9876543210 --user alice
```

Revocation only removes the registry entry; it doesn't wipe the
handshake file from the USB. That's deliberate — the stale file is
inert without a matching registry entry, and leaving it lets you
re-enroll easily.

## Checking enrollment status

```bash
usbypass status
```

Example output when a key is active:

```
Verified-state file: /run/usbypass/state.json
  user     : alice
  serial   : 0123456789ABCD
  devnode  : /dev/sdb1
  verified : 1712350000.0

Enrolled keys:
  alice: primary [0123456789ABCD]
  alice: backup  [FEDCBA9876543210]

Currently attached USB partitions:
  /dev/sdb1  serial=0123456789ABCD  mount=/media/alice/MYKEY  [enrolled]
```

## Common enrollment errors

- **"No USB partitions detected"** — the drive isn't visible to the
  kernel (`lsblk`) or isn't a block partition. Replug or check
  `dmesg`.
- **"<device> has no USB serial number reported by the kernel"** —
  `pyudev` sees the device but no serial. Cheap drives often skip this.
  Try a different stick; serial-less drives have no anti-clone
  protection.
- **"<device> reports a weak/default serial"** — the drive reports
  something like `0000000000` that's unlikely to be unique. Refuses
  by default; override with `--force-weak-serial` if you understand
  the risk (you are now trivially cloneable).
- **"<device> is not mounted"** — mount it. Most desktops auto-mount
  USBs; otherwise: `udisksctl mount -b /dev/sdXN`.
- **"Round-trip check failed"** — disk is read-only or full. Remount
  read-write or free space.

## Filesystem choice

USBYPASS writes a 32-byte file. Any filesystem Linux can write
(`vfat`, `exfat`, `ext4`, `ntfs-3g`, `f2fs`, ...) is fine. A blank
FAT32 stick from any manufacturer works out of the box, and cross-OS
visibility doesn't matter because the handshake file only has meaning
on the host that enrolled it.
