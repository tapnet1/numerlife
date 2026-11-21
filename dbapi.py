"""The database driver: SQLite, encrypted with SQLCipher when NUMER_DB_KEY is set.

With NUMER_DB_KEY (64 hex characters, in .env on the server only), the whole database file and
its backups are encrypted (AES-256): a copied file or backup is unreadable without the key.
Without it (tests, local development) this is plain SQLite. Every module imports the driver from
here (`from dbapi import sqlite as sqlite3`), so exception classes and Row match the connection.

The key is used as a raw key (no password stretching), so opening a connection stays fast.
encrypt_db.py converts an existing plain database (and its backups) once.
"""
from __future__ import annotations

import os
import re

_KEY = os.environ.get("NUMER_DB_KEY", "").strip().lower()
if _KEY and not re.fullmatch(r"[0-9a-f]{64}", _KEY):
    raise RuntimeError("NUMER_DB_KEY must be 64 hexadecimal characters")

if _KEY:
    from sqlcipher3 import dbapi2 as sqlite  # noqa: F401
else:
    import sqlite3 as sqlite  # noqa: F401

ENCRYPTED = bool(_KEY)


def key_pragma() -> str:
    return f"PRAGMA key = \"x'{_KEY}'\""


def connect(path: str, timeout: float = 10):
    connection = sqlite.connect(path, timeout=timeout)
    if ENCRYPTED:
        connection.execute(key_pragma())
    return connection
