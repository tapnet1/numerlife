"""Background jobs, run by cron (installed by the deploy in the numerlife-jobs block).

    .venv/bin/python jobs.py purge-attempts
    .venv/bin/python jobs.py send-push

Jobs load .env through passenger_wsgi.py, like the deploy, and never print secrets.
"""
from __future__ import annotations

import datetime as dt
import sys

RETENTION = dt.timedelta(days=1)


def purge_attempts() -> int:
    """Delete sign-in and form attempt records (hashed addresses) older than a day."""
    from app import initialize, iso, now_utc, open_db

    initialize()
    cutoff = iso(now_utc() - RETENTION)
    with open_db() as connection:
        removed = connection.execute("DELETE FROM login_attempts WHERE created_at < ?", (cutoff,)).rowcount
        removed += connection.execute("DELETE FROM form_attempts WHERE created_at < ?", (cutoff,)).rowcount
    return removed


def send_push() -> dict:
    """Send queued push notifications (new leads); retries anything the request didn't send."""
    import notify

    return notify.send_pending()


JOBS = {"purge-attempts": purge_attempts, "send-push": send_push}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in JOBS:
        print(f"usage: jobs.py {{{', '.join(JOBS)}}}", file=sys.stderr)
        return 2
    import passenger_wsgi  # noqa: F401  (loads .env on the server)
    result = JOBS[argv[1]]()
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    print(f"{stamp} {argv[1]}: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
