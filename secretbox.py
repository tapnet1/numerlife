"""Encrypt stored third-party credentials (e.g. SMTP or OpenAI keys saved in admin).

AES-256-GCM with the base64 32-byte key in NUMER_CREDENTIAL_KEY. Plaintext is never
logged, and errors never include key material or the value being encrypted.
"""
from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_PREFIX = "v1:"


class CredentialKeyError(RuntimeError):
    pass


def _key() -> bytes:
    raw = os.environ.get("NUMER_CREDENTIAL_KEY", "")
    if not raw:
        raise CredentialKeyError("NUMER_CREDENTIAL_KEY is not set")
    try:
        key = base64.b64decode(raw, validate=True)
    except ValueError:
        raise CredentialKeyError("NUMER_CREDENTIAL_KEY is not valid base64") from None
    if len(key) != 32:
        raise CredentialKeyError("NUMER_CREDENTIAL_KEY must decode to 32 bytes")
    return key


def check_key() -> None:
    """Raise CredentialKeyError if the key is missing or malformed (used by the deploy)."""
    _key()


def encrypt(plaintext: str) -> str:
    nonce = os.urandom(12)
    sealed = AESGCM(_key()).encrypt(nonce, plaintext.encode("utf-8"), None)
    return _PREFIX + base64.b64encode(nonce + sealed).decode("ascii")


def decrypt(token: str) -> str:
    if not token.startswith(_PREFIX):
        raise ValueError("Unrecognized encrypted value")
    blob = base64.b64decode(token[len(_PREFIX):])
    return AESGCM(_key()).decrypt(blob[:12], blob[12:], None).decode("utf-8")


if __name__ == "__main__":
    print(base64.b64encode(os.urandom(32)).decode("ascii"))  # generate a new key
