"""Follow-up email series for visitors who unlocked their report and opted in to hear from us.

Seven personalised emails over three weeks (days 1, 3, 5, 8, 11, 15, 21 after the report),
each about one of their own numbers, in the visitor's language (English or Spanish). The
series stops when they unsubscribe, become a client, get archived or have a reading logged.

Opens (a 1×1 image) and clicks (a signed redirect) are recorded in email_events so Admin can
compare the steps. Opens are approximate: some mail apps load images automatically.
"""
from __future__ import annotations

import datetime as dt
import json
import secrets
from zoneinfo import ZoneInfo

from flask import Response, abort, redirect, request
from itsdangerous import BadSignature, URLSafeSerializer

import calendar_content
import calendar_engine as engine
import series_content as sc
from app import BASE_URL, app, db, iso, now_utc, open_db

KIND = "lead_series"
STEPS = (  # (key, days after the report was unlocked)
    ("expression", 1), ("soul_urge", 3), ("year", 5), ("personality", 8),
    ("days", 11), ("harmony", 15), ("last", 21),
)
MIN_GAP = dt.timedelta(hours=40)        # never two series emails closer than this
START_WINDOW = dt.timedelta(days=30)     # leads older than this never start the series (report links last 90)
STOP_STAGES = ("client", "archived")
PIXEL = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00"
         b",\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")


def enabled() -> bool:
    import emails
    return emails.get_setting("lead_series") == "on"


def lead_lang(lead) -> str:
    lang = lead["lang"] if "lang" in lead.keys() else None
    return lang if lang in sc.LANGS else "en"


# --------------------------------------------------------------------------
# Tracking
# --------------------------------------------------------------------------
def _signer() -> URLSafeSerializer:
    return URLSafeSerializer(app.secret_key, salt="numer-click")


def tracked(track: str | None, url: str) -> str:
    if not track:
        return url
    return f"{BASE_URL}/e/c/{_signer().dumps([track, url])}"


def _record(track: str, event: str) -> None:
    with open_db() as connection:
        # only while the recipient still agrees to hear from us: nothing is recorded after an opt-out
        if connection.execute("SELECT 1 FROM email_outbox o JOIN leads l ON l.id = o.lead_id"
                              " WHERE o.track = ? AND l.marketing_opt_in = 1", (track,)).fetchone():
            connection.execute("INSERT INTO email_events (track, event, created_at) VALUES (?, ?, ?)",
                               (track, event, iso(now_utc())))


def record_event(track: str | None, event: str) -> None:
    if track and len(track) <= 64:
        _record(track, event)


@app.get("/e/o/<track>.gif")
def email_open(track):
    record_event(track, "open")
    response = Response(PIXEL, mimetype="image/gif")
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["X-Robots-Tag"] = "noindex"
    return response


@app.get("/e/c/<token>")
def email_click(token):
    try:
        track, url = _signer().loads(token)
    except (BadSignature, ValueError, TypeError):
        abort(404)
    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
        abort(404)
    record_event(str(track), "click")
    response = redirect(url, code=302)
    response.headers["X-Robots-Tag"] = "noindex"
    return response


# --------------------------------------------------------------------------
# Building one email
# --------------------------------------------------------------------------
def _fmt_day(day: dt.date, t: dict) -> str:
    return f"{t['weekdays'][day.weekday()]} {day.day} {t['months'][day.month - 1]}"


def build(lead, practitioner, key: str, track: str | None = None, today: dt.date | None = None):
    """(subject, text, html, headers) for one step, or None when the step doesn't apply."""
    import calendars
    import emails
    lang = lead_lang(lead)
    t = sc.TEXT[lang]
    numbers = json.loads(lead["numbers"])
    name = (lead["full_name"].split() or [""])[0]
    today = today or engine.local_today()
    birth = dt.date.fromisoformat(lead["birth_date"])
    card = emails._card(practitioner) if practitioner is not None and practitioner["is_active"] else None
    pname = card["name"] if card else ""
    booking = card["booking_url"] if card else None
    report_url = f"{BASE_URL}/r/{lead['token']}"
    overrides = calendars.practitioner_overrides(lead["practitioner_id"], lang)

    lp, ex, su, pe = (numbers.get(k) for k in ("life_path", "expression", "soul_urge", "personality"))
    fields = {"name": name, "lp": lp, "ex": ex, "su": su, "year": today.year, "pname": pname,
              "pfirst": (pname.split() or [""])[0]}
    paragraphs, days = [], []
    extra_link = None
    if key in ("expression", "soul_urge", "personality"):
        n = {"expression": ex, "soul_urge": su, "personality": pe}[key]
        if not n:
            return None  # this group of letters isn't active in their name
        title, gift, desire, seen = sc.traits(n, lang)
        fields.update(n=n, title=title, gift=gift, desire=desire, seen=seen)
        paragraphs = [p.format(**fields) for p in t[key]]
    elif key == "year":
        py = engine.personal_year(birth, today.year)
        guide = calendar_content.guidance(py, lang, overrides)
        fields.update(py=py, ytitle=guide["title"], focus=guide["focus"], avoid=guide["avoid"])
        paragraphs = [p.format(**fields) for p in t["year"]]
    elif key == "days":
        pm = engine.personal_month(birth, today.year, today.month)
        fields.update(pm=pm, mtitle=calendar_content.guidance(pm, lang, overrides)["title"])
        paragraphs = [t["days_intro"].format(**fields)]
        for offset in range(1, 15):
            day = today + dt.timedelta(days=offset)
            number = engine.personal_day(birth, day)
            reason = (t["days_same"] if number == lp else t["days_one"] if number == 1
                      else t["days_eight"] if number == 8 else None)
            if reason:
                days.append({"label": _fmt_day(day, t), "number": number, "why": reason.format(**fields)})
        days = days[:6]
        extra_link = {"label": t["get_calendar"], "url": tracked(track, report_url + "#calendar"),
                      "intro": t["days_more"]}
    elif key == "harmony":
        if not ex:
            return None
        fields.update(lptitle=sc.traits(lp, lang)[0], extitle=sc.traits(ex, lang)[0])
        paragraphs = [t["harmony_intro"].format(**fields), t[f"harmony_{sc.relation(lp, ex)}"], t["harmony_more"]]
    elif key == "last":
        paragraphs = [p.format(**fields) for p in t["last"]]
        paragraphs.append((t["last_book"] if booking else t["last_none"]).format(**fields))
        paragraphs.append(t["last_bye"].format(**fields))
    else:
        raise ValueError(key)

    if booking:
        cta = {"label": t["book"].format(**fields), "url": tracked(track, booking)}
        secondary = {"label": t["revisit"], "url": tracked(track, report_url)}
    else:
        cta = {"label": t["see_report"], "url": tracked(track, report_url)}
        secondary = None
    unsubscribe = emails.unsubscribe_url("client", lead["email"]) + (f"?t={track}" if track else "")
    context = {"email_lang": lang, "hi": t["hi"].format(name=name), "paragraphs": paragraphs, "days": days,
               "extra_link": extra_link, "cta": cta, "secondary": secondary, "card": card,
               "card_label": t["card_label"], "card_book_label": t["card_book"], "card_about_label": t["card_about"],
               "card_book_url": tracked(track, booking) if booking else None, "why": t["why"], "unsubscribe_label": t["unsubscribe"],
               "unsubscribe_url": unsubscribe,
               "pixel_url": f"{BASE_URL}/e/o/{track}.gif" if track else None}
    subject = t["subjects"][key].format(**fields)
    text, html = emails._render_pair("series", **context)
    return subject, text, html, {"List-Unsubscribe": f"<{unsubscribe}>"}


def requeue_pending(connection, where: str, params: tuple) -> None:
    """Cancel undelivered series emails for leads matching `where` (e.g. after a reassignment)
    and step them back, so the next run rebuilds them with the new practitioner's details."""
    rows = connection.execute(f"SELECT o.id, o.lead_id FROM email_outbox o JOIN leads ON leads.id = o.lead_id"
                              f" WHERE o.kind = ? AND o.sent_at IS NULL AND {where}", (KIND, *params)).fetchall()
    for row in rows:
        connection.execute("UPDATE email_outbox SET sent_at = ?, last_error = 'cancelled: reassigned' WHERE id = ?",
                           (iso(now_utc()), row["id"]))
        connection.execute("UPDATE leads SET series_step = MAX(series_step - 1, 0), series_sent_at = NULL"
                           " WHERE id = ?", (row["lead_id"],))


# --------------------------------------------------------------------------
# Daily run
# --------------------------------------------------------------------------
def still_wanted(connection, lead_id) -> bool:
    lead = connection.execute("SELECT marketing_opt_in, stage FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if lead is None or not lead["marketing_opt_in"] or lead["stage"] in STOP_STAGES:
        return False
    return connection.execute("SELECT 1 FROM readings WHERE lead_id = ?", (lead_id,)).fetchone() is None


def run(now: dt.datetime | None = None, force: bool = False) -> dict:
    """Queue every series email that's due. Runs once a day from the nudges job."""
    import emails
    stats = {"sent": 0}
    if not force and not enabled():
        stats["skipped"] = "series is off"
        return stats
    now = now or now_utc()
    with app.test_request_context("/", base_url=BASE_URL), open_db() as connection:
        leads = connection.execute(
            "SELECT * FROM leads WHERE status = 'captured' AND marketing_opt_in = 1 AND email IS NOT NULL"
            " AND stage NOT IN ('client', 'archived') AND series_step < ? AND captured_at > ?"
            " AND (series_sent_at IS NULL OR series_sent_at < ?)",
            (len(STEPS), iso(now - START_WINDOW - dt.timedelta(days=STEPS[-1][1])), iso(now - MIN_GAP))).fetchall()
        for lead in leads:
            if not still_wanted(connection, lead["id"]):
                continue
            if connection.execute("SELECT 1 FROM email_outbox WHERE kind = ? AND lead_id = ? AND sent_at IS NULL",
                                  (KIND, lead["id"])).fetchone():
                continue  # the previous email hasn't gone out yet; keep the gap between deliveries
            if lead["series_step"] == 0 and dt.datetime.fromisoformat(lead["captured_at"]) < now - START_WINDOW:
                continue  # too old to start now
            practitioner = connection.execute("SELECT * FROM users WHERE id = ?", (lead["practitioner_id"],)).fetchone() \
                if lead["practitioner_id"] else None
            captured = dt.datetime.fromisoformat(lead["captured_at"])
            step = lead["series_step"]
            while step < len(STEPS):
                key, day = STEPS[step]
                if now < captured + dt.timedelta(days=day):
                    break
                track = secrets.token_urlsafe(16)
                built = build(lead, practitioner, key, track=track,
                              today=now.astimezone(ZoneInfo(engine.DEFAULT_TZ)).date())
                step += 1
                if built is None:
                    continue  # step doesn't apply to this person; move on to the next one
                subject, text, html, headers = built
                emails.enqueue(connection, KIND, lead["email"], subject, text, html, headers, lead_id=lead["id"],
                               campaign=key, track=track)
                connection.execute("UPDATE leads SET series_sent_at = ? WHERE id = ?", (iso(now), lead["id"]))
                stats["sent"] += 1
                break
            connection.execute("UPDATE leads SET series_step = ? WHERE id = ?", (step, lead["id"]))
    if stats["sent"]:
        emails.kick()
    return stats


# --------------------------------------------------------------------------
# Stats for Admin and the lead page
# --------------------------------------------------------------------------
def step_stats() -> list[dict]:
    rows = db().execute(
        "SELECT o.campaign, COUNT(*) AS queued, SUM(o.sent_at IS NOT NULL AND o.last_error = '')"
        " AS sent,"
        " SUM(EXISTS (SELECT 1 FROM email_events e WHERE e.track = o.track AND e.event IN ('open', 'click')))"
        " AS opened,"
        " SUM(EXISTS (SELECT 1 FROM email_events e WHERE e.track = o.track AND e.event = 'click')) AS clicked,"
        " SUM(EXISTS (SELECT 1 FROM email_events e WHERE e.track = o.track AND e.event = 'unsubscribe'))"
        " AS unsubscribed FROM email_outbox o WHERE o.kind = ? GROUP BY o.campaign", (KIND,)).fetchall()
    by_key = {row["campaign"]: row for row in rows}
    out = []
    for number, (key, day) in enumerate(STEPS, start=1):
        row = by_key.get(key)
        sent = (row["sent"] or 0) if row else 0
        opened = (row["opened"] or 0) if row else 0
        clicked = (row["clicked"] or 0) if row else 0
        out.append({"number": number, "key": key, "day": day, "sent": sent, "opened": opened, "clicked": clicked,
                    "unsubscribed": (row["unsubscribed"] or 0) if row else 0,
                    "open_rate": round(100 * opened / sent) if sent else None,
                    "click_rate": round(100 * clicked / sent) if sent else None})
    return out


def conversions() -> dict:
    """Leads who got at least one series email, and how many of them went on to book or become clients."""
    row = db().execute(
        "SELECT COUNT(*) AS reached,"
        " SUM(l.client_at IS NOT NULL OR l.stage = 'client' OR EXISTS (SELECT 1 FROM readings r WHERE r.lead_id = l.id))"
        " AS converted"
        " FROM leads l WHERE EXISTS (SELECT 1 FROM email_outbox o WHERE o.kind = ? AND o.lead_id = l.id"
        " AND o.sent_at IS NOT NULL AND o.last_error = '')", (KIND,)).fetchone()
    return {"reached": row["reached"] or 0, "converted": row["converted"] or 0}


def lead_progress(lead_id: int) -> dict:
    rows = db().execute(
        "SELECT o.campaign, o.sent_at, o.last_error,"
        " EXISTS (SELECT 1 FROM email_events e WHERE e.track = o.track AND e.event IN ('open', 'click')) AS opened,"
        " EXISTS (SELECT 1 FROM email_events e WHERE e.track = o.track AND e.event = 'click') AS clicked"
        " FROM email_outbox o WHERE o.kind = ? AND o.lead_id = ? ORDER BY o.id", (KIND, lead_id)).fetchall()
    sent = [r for r in rows if r["sent_at"] and not r["last_error"]]  # delivered (not cancelled or expired)
    return {"sent": len(sent), "total": len(STEPS), "opened": sum(r["opened"] for r in sent),
            "clicked": sum(r["clicked"] for r in sent)}


STEP_LABELS = {"expression": "Expression number", "soul_urge": "Soul Urge", "year": "Personal year",
               "personality": "Personality", "days": "Best days ahead", "harmony": "Life Path × Expression",
               "last": "Last note"}

app.jinja_env.globals.update(series_step_stats=step_stats, series_conversions=conversions,
                             series_lead_progress=lead_progress, series_enabled=enabled,
                             series_step_labels=STEP_LABELS, series_languages={"en": "English", "es": "Español"})
