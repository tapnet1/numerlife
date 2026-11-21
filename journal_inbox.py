"""Practitioner's journal inbox (/journal): every client's journal in one place.

Left: clients with journal activity (search by name or email; unread first, then most recent).
Right: the chosen client's notes in date order, each with its conversation and a reply box;
filter to one journal date. Opening a client marks their notes as read. Team members see their
practitioner's clients (lead scope). Notes a client keeps private (journal sharing off) aren't shown.
"""
from __future__ import annotations

from flask import render_template, request

import feedback
from app import app, current_user, db, login_required


def clients(user, q: str = "") -> list:
    import workspace
    condition, params = workspace.lead_scope(user)
    clauses, values = [condition, feedback.SHARED_SQL], list(params)
    if q:
        like = f"%{q}%"
        clauses.append("(leads.full_name LIKE ? OR leads.email LIKE ?)")
        values += [like, like]
    return db().execute(
        "SELECT leads.id, leads.full_name, leads.email,"
        " SUM(feedback_entries.seen_at IS NULL) AS unread, COUNT(*) AS notes,"
        " MAX(COALESCE((SELECT MAX(r.created_at) FROM journal_replies r WHERE r.entry_id = feedback_entries.id),"
        "              feedback_entries.created_at)) AS latest"
        " FROM feedback_entries JOIN leads ON leads.id = feedback_entries.lead_id"
        f" WHERE {' AND '.join(clauses)} GROUP BY leads.id ORDER BY unread > 0 DESC, latest DESC LIMIT 200",
        values).fetchall()


def unread_total(user) -> int:
    import workspace
    condition, params = workspace.lead_scope(user)
    return db().execute(
        "SELECT COUNT(*) FROM feedback_entries JOIN leads ON leads.id = feedback_entries.lead_id"
        f" WHERE feedback_entries.seen_at IS NULL AND {condition} AND {feedback.SHARED_SQL}", params).fetchone()[0]


def _journal_unread():
    user = current_user()
    if user is None:
        return 0
    try:
        return unread_total(user)
    except Exception:  # never break the menu
        return 0


app.jinja_env.globals["journal_unread"] = _journal_unread


@app.get("/journal")
@login_required
def journal_inbox():
    import calendar_journal
    import workspace
    user = current_user()
    q = (request.args.get("q") or "").strip()[:100]
    rows = clients(user, q)
    selected = None
    entries, replies, dates, day = [], {}, [], ""
    client_id = request.args.get("client", type=int)
    if client_id:
        selected = workspace.scoped_lead(client_id)
        all_entries = db().execute(
            f"SELECT * FROM feedback_entries WHERE lead_id = ? AND {feedback.SHARED_SQL}"
            " ORDER BY entry_date, created_at, id", (client_id,)).fetchall()
        dates = sorted({e["entry_date"] for e in all_entries}, reverse=True)
        day = request.args.get("date") or ""
        entries = [e for e in all_entries if not day or e["entry_date"] == day]
        replies = calendar_journal.replies_for([e["id"] for e in entries])
    html = render_template("journal_inbox.html", rows=rows, q=q, selected=selected, entries=entries,
                           replies=replies, dates=dates, day=day,
                           cal=db().execute("SELECT * FROM calendars WHERE lead_id = ?", (client_id,)).fetchone()
                           if client_id else None)
    if selected is not None:
        feedback.mark_seen(selected["id"])  # after rendering, so new notes still show as New on this view
    return html
