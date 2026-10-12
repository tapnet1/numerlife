"""Web Push (RFC 8030) with VAPID (RFC 8292) and aes128gcm payload encryption (RFC 8291).

Adapted from Strategy Capital. Built on `cryptography` and the standard library only.

Key lives in the server .env (never in the repo), created once by the deploy:
    NUMER_VAPID_PRIVATE_KEY   base64url of the raw 32-byte P-256 private value
The public key is derived from it. `python webpush.py generate` prints a new private key.
Rotating the key means every device has to turn notifications on again.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ENV_KEY = "NUMER_VAPID_PRIVATE_KEY"
# Browser push services only: the server never posts to an address a client made up.
PUSH_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com")
PUSH_SUFFIXES = (".push.services.mozilla.com", ".push.apple.com", ".notify.windows.com")


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    text = text.strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def generate_private_key() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return b64u(key.private_numbers().private_value.to_bytes(32, "big"))


def _private_key(value: str | None = None):
    value = value or os.environ.get(ENV_KEY, "")
    if not value:
        return None
    raw = unb64u(value)
    if len(raw) != 32:
        raise ValueError(f"{ENV_KEY} must be 32 bytes, base64url")
    return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())


def _point(public_key) -> bytes:
    return public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def public_key(value: str | None = None) -> str | None:
    """Application server key for PushManager.subscribe, or None when push isn't configured."""
    key = _private_key(value)
    return b64u(_point(key.public_key())) if key else None


def endpoint_allowed(endpoint: str) -> bool:
    parts = urlsplit(endpoint or "")
    host = (parts.hostname or "").lower()
    return (parts.scheme == "https" and parts.port in (None, 443) and len(endpoint) <= 1024
            and (host in PUSH_HOSTS or host.endswith(PUSH_SUFFIXES)))


def _hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:length]  # length <= 32


def encrypt(payload: bytes, p256dh: str, auth: str, salt: bytes | None = None, server_key=None) -> bytes:
    """aes128gcm body for one subscription (RFC 8291), as a single record."""
    ua_public = unb64u(p256dh)
    auth_secret = unb64u(auth)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _point(server_key.public_key())
    shared = server_key.exchange(ec.ECDH(), ua_key)
    ikm = _hkdf_expand(_hkdf_extract(auth_secret, shared), b"WebPush: info\x00" + ua_public + as_public, 32)
    salt = salt or os.urandom(16)
    prk = _hkdf_extract(salt, ikm)
    cek = _hkdf_expand(prk, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf_expand(prk, b"Content-Encoding: nonce\x00", 12)
    body = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack("!IB", 4096, len(as_public)) + as_public + body


def vapid_header(endpoint: str, subject: str, key=None, now: float | None = None) -> str:
    key = key or _private_key()
    parts = urlsplit(endpoint)
    claims = {"aud": f"{parts.scheme}://{parts.netloc}", "exp": int((now or time.time()) + 12 * 3600),
              "sub": subject}
    signing_input = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode()) + "." + \
        b64u(json.dumps(claims, separators=(",", ":")).encode())
    r, s = decode_dss_signature(key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
    token = signing_input + "." + b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={token}, k={b64u(_point(key.public_key()))}"


def subject() -> str:
    base = os.environ.get("NUMER_BASE_URL", "").rstrip("/")
    return base if base.startswith("https://") else "mailto:reports@numer.life"


def _post(url: str, data: bytes, headers: dict, timeout: int):
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(200).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(200).decode("utf-8", "replace")


def send(subscription: dict, message: dict, ttl: int = 3600, post=None) -> tuple[int, str]:
    """POST one encrypted message. Returns (HTTP status, short reason); 404/410 mean the
    subscription is gone. Network errors raise. Never logs keys or the endpoint's secret path."""
    if not endpoint_allowed(subscription["endpoint"]):
        return 400, "unsupported push service"
    body = encrypt(json.dumps(message).encode(), subscription["p256dh"], subscription["auth"])
    headers = {"Authorization": vapid_header(subscription["endpoint"], subject()), "TTL": str(ttl),
               "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "Urgency": "high"}
    status, text = (post or _post)(subscription["endpoint"], body, headers, 15)
    return status, " ".join((text or "").split())[:160]


if __name__ == "__main__":
    if sys.argv[1:] == ["generate"]:
        print(generate_private_key())
    else:
        print("usage: python webpush.py generate", file=sys.stderr)
        sys.exit(2)
