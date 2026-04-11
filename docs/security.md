# Security model

This document describes what USBYPASS does and does not protect
against, the assets involved, and the threats it defends against.
**Read this before you rely on USBYPASS for anything important.**

## Assets

- **`/etc/usbypass/secret.key`** — 64 random bytes, root-only, `0600`.
  Knowledge of this secret allows an attacker to forge a handshake for
  any (username, serial) pair. If it leaks, `rm /etc/usbypass/secret.key`
  and re-run `sudo ./install.sh` to regenerate, then re-enroll every
  USB key.

- **`/var/lib/usbypass/enrolled.json`** — public metadata (serial,
  label, enrolled_at per user). Not a secret; its integrity matters
  only insofar as an attacker who can write to it can enroll their own
  serial, which requires root.

- **`/run/usbypass/state.json`** — ephemeral, regenerated on every
  insert. Contains the currently-verified user+serial. An attacker
  with root can forge it, but root already owns the system.

- **USB handshake file (`<mount>/.usbypass/handshake`)** — 32 bytes,
  HMAC-SHA256. Not secret — its value is deterministic given the
  secret and (user, serial), but producing it requires the secret.

## Threat model

USBYPASS is designed to defend against:

| Threat                                                        | Defended? | How                                                           |
|---------------------------------------------------------------|-----------|---------------------------------------------------------------|
| **Bit-for-bit filesystem clone** (`dd if=/dev/sdX of=/dev/sdY`) | Yes       | Cloned drive has a different controller serial → HMAC mismatch |
| **Handshake file tampering**                                  | Yes       | HMAC verification fails                                       |
| **Swapping handshake to another user's drive**                | Yes       | HMAC input includes username                                  |
| **Stolen USB but no host access**                             | N/A       | The attacker needs physical host access too                   |
| **Stale sudo credentials after unplug**                       | Yes (best effort) | udev remove → systemd clears `/run/sudo/ts/*`                 |
| **USB plugged into a different host**                         | Yes       | The other host has its own secret; HMAC won't verify          |
| **Offline brute force of `/etc/usbypass/secret.key`**         | Yes       | Secret is 64 random bytes — far beyond brute-force range     |

USBYPASS does **not** defend against:

| Threat                                                        | Why                                                          |
|---------------------------------------------------------------|--------------------------------------------------------------|
| **Physical theft of the USB key**                             | By design — the USB *is* the authentication factor          |
| **Root compromise**                                           | Root can read the secret, forge state, or modify PAM        |
| **Malicious firmware on the USB controller**                  | The stick could spoof its own serial. Use a trusted vendor. |
| **Attacker with write access to `/usr/local/libexec/usbypass-pam-helper`** | They already have root, so no new boundary crossed          |
| **Cold-boot attacks on RAM** (secret lives in memory during verify) | Out of scope; use TPM-backed storage if this matters        |
| **Malware replaying a captured state.json**                   | Only root can read/write `/run/usbypass/state.json`         |
| **Early-boot / LUKS unlock**                                  | PAM runs post-boot; disk encryption is a separate concern   |
| **SSH login bypass** (unless you explicitly wire it up)       | We do not edit `/etc/pam.d/sshd` directly                  |

## Cryptographic design

**Primitive**: HMAC-SHA256.

**Key**: 64 random bytes from `secrets.token_bytes(64)`, written atomically
to `/etc/usbypass/secret.key` with mode `0600`, owner `root:root`. The
directory `/etc/usbypass/` is mode `0700`.

**Message**: UTF-8 encoding of `"{username}:{serial}"`.

**Output**: 32 bytes written verbatim to `<USB>/.usbypass/handshake`.

**Verification**: recompute, compare with `hmac.compare_digest` (constant
time).

**Why HMAC and not a signature?** We don't need public verifiability.
Only the host needs to verify, and the host holds the secret. HMAC is
simpler, faster, and has smaller key material. Moving to Ed25519 would
only add value if we wanted cross-host enrollments, which is explicitly
out of scope.

**Why include the username?** To prevent re-enrollment attacks where a
root-privileged attacker copies an existing user's `enrolled.json` entry
into a different user's slot. Without the username in the HMAC input,
the same handshake file would authenticate any user. With it, rebinding
a key to a new user requires the secret, which root has anyway — so the
additional protection is against *non-root* tampering with
`enrolled.json` (currently impossible due to file permissions, but a
good belt-and-braces measure in case a future change loosens them).

## Hardening choices

- **PAM hot path imports are minimal.** `verify.py` imports only
  `crypto`, `state`, and the fast-path subset of `usb` — total import
  cost under 30 ms. No pyudev, no logging, no subprocess.
- **`hmac.compare_digest` everywhere.** We never `==` secret data.
- **Atomic writes**. The secret, enrollment registry, state file, and
  handshake file are all written via tmpfile + `os.replace` to avoid
  half-written corruption.
- **Permission enforcement on load.** `crypto.load_secret` refuses to
  read the key if group/other bits are set; this turns an accidental
  `chmod 644` into a loud failure rather than silent disclosure.
- **Weak-serial refusal.** `usbypass enroll` refuses drives that report
  blank or obviously-default serials, because the anti-clone guarantee
  evaporates if every drive of that model reports the same serial.
- **systemd hardening** on `usbypass-clear-sudo.service`:
  `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes`,
  `NoNewPrivileges=yes`, `ReadWritePaths` limited to the sudo
  timestamp directories.

## What to audit

If you are reviewing USBYPASS for production use, focus on:

1. **`crypto.py`** — the HMAC implementation and secret I/O. Should fit
   on one page.
2. **`verify.py`** — the PAM hot path. ~30 lines.
3. **`handler.py`** — the udev event handler. Especially
   `clear_sudo_timestamps` and the failure modes of `wait_for_mount`.
4. **`pam_installer.py`** — verify that the generated PAM line uses
   `success=done default=ignore` and that the marker block is correctly
   removed on uninstall.
5. **`install.sh`** — verify file permissions (`0755` for libexec
   scripts, `0600` for the secret, `0644` for the udev rule).

## Responsible disclosure

If you find a vulnerability, please file an advisory at
`https://github.com/mutkuoz/usbypass/security/advisories` rather than
opening a public issue.
