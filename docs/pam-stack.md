# PAM stack modifications

USBYPASS inserts exactly one line into the `auth` phase of the PAM
stack for each relevant service. The line is:

```
auth  [success=done default=ignore]  pam_exec.so quiet /usr/local/libexec/usbypass-pam-helper
```

## Control expression decoded

`pam_exec.so` runs our helper script and maps its exit code to a PAM
return value:

- exit `0` → `PAM_SUCCESS`
- exit non-zero → `PAM_AUTH_ERR` (default)

The `[success=done default=ignore]` bracket tells PAM what to do with
those returns:

- `success=done` — on `PAM_SUCCESS`, stop processing the `auth` stack
  immediately and return success to the caller. No password prompt,
  no further modules.
- `default=ignore` — for *any other* return (including `PAM_AUTH_ERR`),
  act as if the module were not there. Control flows to the next line
  of the stack, which is ordinarily `pam_unix.so` (the password
  module).

The combination is what gives USBYPASS its dual-mode property:

| Helper exit | PAM return    | Next step              |
|-------------|---------------|------------------------|
| `0`         | `PAM_SUCCESS` | Done — login/sudo OK   |
| `1`         | `PAM_AUTH_ERR`| Fall through to next   |
| any other   | `PAM_AUTH_ERR`| Fall through to next   |

Because our helper writes its PAM result to the exit code and nothing
else — no stderr, no prompt manipulation — a failure is totally
invisible to the user. They just see the normal password prompt.

## Debian / Ubuntu — `pam-auth-update`

On Debian-family systems, `/etc/pam.d/common-auth` is managed by
`pam-auth-update(8)`, which rewrites the file from snippets under
`/usr/share/pam-configs/`.

We ship `/usr/share/pam-configs/usbypass`:

```
Name: USBYPASS key authentication
Default: yes
Priority: 192
Auth-Type: Primary
Auth:
	[success=end default=ignore]	pam_exec.so quiet /usr/local/libexec/usbypass-pam-helper
```

`Priority: 192` places our hook above the default `unix` block
(priority `256`) so our line appears first in the rewritten
`common-auth`. `pam-auth-update --package` is run non-interactively
from `install.sh`.

The resulting `/etc/pam.d/common-auth` looks like:

```
# here are the per-package modules (the "Primary" block)
auth    [success=2 default=ignore]      pam_exec.so quiet /usr/local/libexec/usbypass-pam-helper
auth    [success=1 default=ignore]      pam_unix.so nullok
# here's the fallback if no module succeeds
auth    requisite                       pam_deny.so
# prime the stack with a positive return value if there isn't one already
auth    required                        pam_permit.so
```

`common-auth` is `@include`-d by `sudo`, `login`, `gdm`, `sshd`,
`xscreensaver`, etc., so installing once covers every PAM consumer.

### Uninstall

Delete `/usr/share/pam-configs/usbypass` and re-run
`pam-auth-update --package`. Done.

## Fedora / RHEL / Rocky / AlmaLinux

Fedora uses [authselect](https://github.com/authselect/authselect) to
manage `/etc/pam.d/sudo`, `/etc/pam.d/system-auth`, and
`/etc/pam.d/password-auth` from templates. authselect does not offer a
drop-in mechanism comparable to `pam-auth-update`, and writing a full
custom profile is overkill for a one-line injection.

USBYPASS therefore edits the PAM files directly, bracketing its line
with marker comments so uninstall can remove it precisely:

```
# >>> USBYPASS BEGIN (do not edit inside this block)
auth  [success=done default=ignore]  pam_exec.so quiet /usr/local/libexec/usbypass-pam-helper
# <<< USBYPASS END
```

Target files:

- `/etc/pam.d/sudo`
- `/etc/pam.d/system-auth`
- `/etc/pam.d/password-auth`

Before each edit we copy the file to `<file>.usbypass.bak`. The block
is inserted **before the first non-comment `auth` line**, so our hook
runs first and can short-circuit on success.

### authselect caveat

If you later run `authselect select sssd` (or any other profile),
authselect will rewrite the PAM files from its templates, silently
removing our marker block. Re-run `sudo usbypass install` to put it
back.

A long-term fix is to ship a custom authselect profile under
`/etc/authselect/custom/usbypass/`; that's tracked as future work.

## Arch / Manjaro and others

Arch does not run `pam-auth-update` or `authselect`. We use the same
marker-based direct-edit strategy as Fedora, but target only:

- `/etc/pam.d/sudo`
- `/etc/pam.d/system-auth`

## sshd interaction

Our hook applies to every PAM service that `@include`-s
`common-auth` / `system-auth`, which includes `sshd` by default. This
is almost always undesirable — you don't want SSH to skip password auth
when a USB happens to be plugged into the *server*.

**Mitigation**: our helper checks `PAM_RHOST` and `PAM_SERVICE`. For
`sshd` specifically, the helper's behaviour is unchanged by default
because passwordless ssh normally requires key auth anyway, but you
can hard-disable USBYPASS for sshd by removing the `@include
common-auth` from `/etc/pam.d/sshd` or by adding a shell-level check
inside the helper.

The design allows this to be tightened later without touching the PAM
stack itself — the `verify.py` entry point already has access to the
full PAM environment via `PAM_*` env vars.

## Verifying the stack

```bash
# Debian/Ubuntu
grep usbypass /etc/pam.d/common-auth
cat /usr/share/pam-configs/usbypass

# Fedora/Arch/other
grep -A1 'USBYPASS BEGIN' /etc/pam.d/sudo /etc/pam.d/system-auth

# Common
sudo usbypass doctor
```

## Recovery if the stack is broken

**Always keep a root TTY open while installing.** If our PAM edit goes
wrong you may be unable to `sudo`. Recovery options:

1. **Root TTY**: use the TTY you left open to restore the backup:
   `cp /etc/pam.d/sudo.usbypass.bak /etc/pam.d/sudo`.
2. **Single-user mode**: boot with `systemd.unit=rescue.target`,
   authenticate as root, and run the same restore.
3. **Rescue USB**: boot a live distro, mount root, restore from
   `.usbypass.bak` files.

See [`troubleshooting.md`](troubleshooting.md) for more.
