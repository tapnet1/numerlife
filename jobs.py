"""Background jobs, run by cron (installed by the deploy in the numerlife-jobs block).

    .venv/bin/python jobs.py purge-attempts
    .venv/bin/python jobs.py send-push
    .venv/bin/python jobs.py send-email
    .venv/bin/python jobs.py send-nudges
    .venv/bin/python jobs.py accrue-referrals
    .venv/bin/python jobs.py calendar-emails

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


def send_email() -> dict:
    """Send queued email (reports, lead alerts, nudges); retries anything not yet sent."""
    import emails

    return emails.send_pending()


def send_nudges() -> dict:
    """Queue reminder emails, if an admin has turned automatic nudges on."""
    import emails

    import lead_series

    result = emails.run_nudges()
    result["series"] = lead_series.run()
    return result


def accrue_referrals() -> dict:
    """Credit this month's CAD $1 referral rewards (once per paying practitioner per month)."""
    import billing

    return billing.accrue_referrals()


def calendar_emails() -> dict:
    """Queue clients' daily and monthly calendar emails once their local morning has started."""
    import calendar_mail
    import emails

    result = calendar_mail.run()
    emails.send_pending()
    return result


JOBS = {"purge-attempts": purge_attempts, "send-push": send_push, "send-email": send_email,
        "send-nudges": send_nudges, "accrue-referrals": accrue_referrals,
        "calendar-emails": calendar_emails}


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
