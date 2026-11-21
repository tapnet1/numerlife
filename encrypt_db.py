"""One-time conversion of the plain SQLite database (and its .bak backups) to SQLCipher.

Run by the deploy, before the key is written to .env:
    NUMER_DB_KEY=<64 hex> .venv/bin/python encrypt_db.py /home/numerlife1/data/numerlife.db
For each file: if it is a plain SQLite database, export it to an encrypted copy, check the copy
(opens with the key, integrity ok, same row counts in every table) and only then replace the
original. The live database is exported while writers are held off (BEGIN IMMEDIATE). Files that
are already encrypted are left alone. Prints no secrets. Exit code 0 = live database encrypted.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from pathlib import Path


def is_plain(path: Path) -> bool:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            connection.execute("SELECT count(*) FROM sqlite_master").fetchone()
            return True
        finally:
            connection.close()
    except sqlite3.DatabaseError:
        return False


def _counts(connection) -> dict:
    tables = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'"
                                               " AND name NOT LIKE 'sqlite_%'")]
    return {t: connection.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in tables}


def encrypt(path: Path, key: str, lock: bool) -> None:
    from sqlcipher3 import dbapi2 as cipher
    tmp = path.with_name(path.name + ".encrypting")
    if tmp.exists():
        tmp.unlink()
    plain = cipher.connect(str(path), timeout=30, isolation_level=None)
    holder = None
    try:
        plain.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        if lock:  # a second connection holds off writers (readers still work) until the swap
            holder = sqlite3.connect(str(path), timeout=30, isolation_level=None)
            holder.execute("BEGIN IMMEDIATE")
        plain.execute(f"ATTACH DATABASE ? AS enc KEY \"x'{key}'\"", (str(tmp),))
        plain.execute("SELECT sqlcipher_export('enc')")
        plain.execute("DETACH DATABASE enc")
        before = _counts(plain)
        check = cipher.connect(str(tmp))
        try:
            check.execute(f"PRAGMA key = \"x'{key}'\"")
            if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError(f"integrity check failed for the encrypted copy of {path.name}")
            if _counts(check) != before:
                raise RuntimeError(f"row counts differ in the encrypted copy of {path.name}")
        finally:
            check.close()
        os.chmod(tmp, 0o600)
        plain.close()
        os.replace(tmp, path)
        for suffix in ("-wal", "-shm"):
            leftover = path.with_name(path.name + suffix)
            if leftover.exists():
                leftover.unlink()
    finally:
        if holder is not None:
            holder.close()
        plain.close()
        if tmp.exists():
            tmp.unlink()


def main(argv: list[str]) -> int:
    key = os.environ.get("NUMER_DB_KEY", "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", key):
        print("NUMER_DB_KEY (64 hex characters) is required", file=sys.stderr)
        return 2
    live = Path(argv[1])
    for backup in sorted(live.parent.glob(live.name + ".*.bak")):
        if is_plain(backup):
            try:
                encrypt(backup, key, lock=False)
                print(f"Backup encrypted: {backup.name}")
            except Exception as exc:  # noqa: BLE001
                print(f"::warning::Could not encrypt backup {backup.name}: {exc}")
    if live.exists() and is_plain(live):
        encrypt(live, key, lock=True)
        print(f"Database encrypted: {live.name}")
    elif not live.exists():
        print("No database yet; it will be created encrypted")
    else:
        print("Database already encrypted")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
