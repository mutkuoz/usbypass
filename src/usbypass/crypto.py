"""Crypto primitives for USBYPASS.

We deliberately keep the surface tiny:

- A 64-byte root secret lives at ``/etc/usbypass/secret.key``.
- A handshake is ``HMAC-SHA256(secret, username || ':' || usb_serial)``.
- Verification is a constant-time compare.

There is no asymmetric crypto here on purpose. A clone of the USB
filesystem won't authenticate because the live controller serial is
fed into the HMAC at verification time — and the secret never leaves
the host.
"""

from __future__ import annotations

import hmac
import os
import secrets
import stat
from hashlib import sha256
from pathlib import Path

from usbypass.config import SECRET_PATH

SECRET_BYTES = 64
HANDSHAKE_BYTES = 32  # SHA-256 output


class SecretMissingError(RuntimeError):
    """Raised when the host secret has not been initialized."""


def generate_secret(path: Path = SECRET_PATH, *, force: bool = False) -> Path:
    """Create the host secret file. Idempotent unless ``force=True``."""
    path = Path(path)
    if path.exists() and not force:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except PermissionError:
        pass
    # Write to a temp file then rename atomically so we never leave a
    # half-written secret behind.
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        f.write(secrets.token_bytes(SECRET_BYTES))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def load_secret(path: Path = SECRET_PATH) -> bytes:
    path = Path(path)
    try:
        with open(path, "rb") as f:
            data = f.read()
    except FileNotFoundError as exc:
        raise SecretMissingError(
            f"USBYPASS secret not found at {path}. Run `usbypass install` or "
            "`install.sh` as root to generate it."
        ) from exc

    # Defensive: refuse dangerously loose permissions. If another user can
    # read the secret, the whole scheme is dead.
    st = os.stat(path)
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SecretMissingError(
            f"Refusing to load secret {path}: permissions too permissive "
            f"(mode={oct(st.st_mode & 0o777)}). Expected 0600."
        )
    if len(data) < 32:
        raise SecretMissingError(
            f"Secret at {path} is truncated ({len(data)} bytes). Regenerate it."
        )
    return data


def compute_handshake(username: str, serial: str, *, secret: bytes | None = None) -> bytes:
    """Return the deterministic HMAC handshake payload."""
    if not username:
        raise ValueError("username must not be empty")
    if not serial:
        raise ValueError("serial must not be empty")
    if secret is None:
        secret = load_secret()
    msg = f"{username}:{serial}".encode("utf-8")
    return hmac.new(secret, msg, sha256).digest()


def verify_handshake(
    username: str,
    serial: str,
    stored: bytes,
    *,
    secret: bytes | None = None,
) -> bool:
    """Constant-time verification.

    Returns True iff ``stored`` matches the expected HMAC for
    ``(username, serial)`` under the host secret. Any exception (missing
    secret, invalid input) results in False — callers treat that as
    "fall through to password".
    """
    if not isinstance(stored, (bytes, bytearray)) or len(stored) != HANDSHAKE_BYTES:
        return False
    try:
        expected = compute_handshake(username, serial, secret=secret)
    except (ValueError, SecretMissingError):
        return False
    return hmac.compare_digest(expected, bytes(stored))
