# Troubleshooting and recovery

## "I can't `sudo` after installing USBYPASS"

**Don't panic.** The installer always takes backups and PAM is designed
to fail open (fall through) when our helper can't be invoked. But if
you do end up locked out, here's the recovery ladder:

### 1. You still have a root shell open

Run, as root:

```bash
# Debian/Ubuntu
rm /usr/share/pam-configs/usbypass
pam-auth-update --package

# Fedora/RHEL/Arch/other
cp /etc/pam.d/sudo.usbypass.bak /etc/pam.d/sudo
cp /etc/pam.d/system-auth.usbypass.bak /etc/pam.d/system-auth
# (only files that existed will have backups)
```

Then verify with `grep usbypass /etc/pam.d/*` — there should be no
matches.

### 2. You have a normal shell but no root shell

Try `sudo -k; sudo echo hi` — if our PAM helper is still correctly
falling through on helper-absent, you'll get a password prompt and
can `sudo ./uninstall.sh`.

### 3. You can't `sudo` at all

Boot into rescue mode:

```
# At the GRUB prompt, press `e`, append to the linux line:
systemd.unit=rescue.target
# Ctrl+X to boot.
```

Log in as root, then restore the backup files as in step 1.

### 4. Rescue target is unavailable

Boot a live USB / live DVD, mount your root partition, and restore
the `.usbypass.bak` files directly from the mounted filesystem.

## "The USB is plugged in but login still asks for a password"

Run `usbypass status`. Diagnose from the output:

- **"(no active USB key)"** — the udev handler either didn't run or
  didn't verify. Check:
  - Is the USB's serial actually enrolled? `usbypass list`.
  - Is the handshake file present? `ls <mount>/.usbypass/handshake`.
  - Is the drive mounted? `lsblk`.
  - Did udev see the event? `udevadm monitor --subsystem-match=block`
    (re-plug to watch events) and
    `journalctl -t usbypass` to see our handler's log lines.
- **State shows a different user** — the key is bound to someone
  else. Re-enroll for the intended user.
- **State looks correct** — check PAM:
  - Debian: `grep usbypass /etc/pam.d/common-auth`
  - Fedora/Arch: `grep usbypass /etc/pam.d/sudo`
  - Run `sudo -k` then `sudo echo hi` and watch `journalctl -t
    usbypass` for helper errors.

## "It worked, then suddenly stopped after a distro upgrade"

Most likely the PAM stack was regenerated:

- **Fedora**: if you ran `authselect select ...`, the templates
  overwrote our markers. Re-run `sudo usbypass install` to
  reinstall the hook.
- **Debian**: `pam-auth-update` may have removed our config if the
  package was partially uninstalled. `sudo ./install.sh` again.
- **Arch**: `pam` package updates may rewrite files under
  `/etc/pam.d/`. The `.pacnew` files should have our markers; merge
  them back.

## "USB is removed but my sudo still doesn't prompt for a password"

Diagnose:

```bash
ls /run/sudo/ts/ /var/run/sudo/ts/ /var/db/sudo/ 2>/dev/null
```

If your user's timestamp file is still there, the removal hook didn't
run. Try:

```bash
sudo systemctl status usbypass-clear-sudo.service
journalctl -u usbypass-clear-sudo.service --since "10 min ago"
journalctl -t usbypass --since "10 min ago"
```

Manual clear:

```bash
sudo -k                  # user-scoped
sudo rm /run/sudo/ts/*   # root-level, covers all users
```

Next `sudo` should prompt.

## "USB not recognised at all by `usbypass enroll`"

```bash
lsblk                              # confirm the kernel sees it
udevadm info --query=all --name=/dev/sdX1 | grep -E 'ID_BUS|ID_SERIAL'
```

USBYPASS filters on `ID_BUS=usb`. Some USB-over-SATA adapters report
`ID_BUS=ata` instead — that's a kernel-level thing we can't work
around from userspace. Use a native USB stick.

## "Clone of my USB works — anti-clone protection isn't working"

This means both drives report the same serial. Check:

```bash
udevadm info --query=property --name=/dev/sdX1 | grep ID_SERIAL
udevadm info --query=property --name=/dev/sdY1 | grep ID_SERIAL
```

If they match, the drives are from a counterfeit batch that hard-codes
its serial. You should:

1. Stop relying on this drive as a security key.
2. Re-enroll with a drive from a reputable manufacturer.

USBYPASS's weak-serial detection catches the obvious cases
(`0000000000` etc.) but can't distinguish legitimately-unique serials
from ones that just happen to be unique within your sample size of one.

## "Login hangs for ~5 seconds after unplugging"

This is a bug — the PAM helper should never take more than a few tens
of milliseconds. Time it:

```bash
PAM_USER="$USER" time /usr/local/libexec/usbypass-pam-helper
```

Expected: under 100 ms. If it's slower, the culprit is usually:

- **Stale NFS mounts in `/proc/self/mountinfo`** — our fast path reads
  mountinfo and walks it. A stuck NFS mount can stall the `read(2)`.
  Unmount the NFS share or add it to `autofs`.
- **`/sys` walk stuck on a disconnecting drive** — happens if you
  remove the USB *during* authentication. Rare; the helper has a
  short bounded walk so this should self-resolve.

## Regenerating the host secret

If you think the secret has leaked:

```bash
sudo rm /etc/usbypass/secret.key
sudo ./install.sh                       # regenerates
# Re-enroll every USB key:
sudo usbypass enroll --user "$USER"
```

Existing handshake files become invalid automatically because the new
HMAC key is different.

## Where to look for logs

```bash
journalctl -t usbypass                 # all USBYPASS syslog entries
journalctl -u usbypass-clear-sudo      # systemd oneshot
udevadm monitor --subsystem-match=block   # live udev events
sudo usbypass doctor                   # installation sanity
sudo usbypass status                   # current state
```
