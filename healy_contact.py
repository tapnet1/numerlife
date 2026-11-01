"""Send a client's profile to the Healy app, on demand, through the iPad's (or phone's) Contacts.

Healy's analysis app has no API or file import; its only way in is Clients > "Import from
Address Book". So the lead page offers "Add to Contacts": a vCard (.vcf) with the client's name,
birth date, email, phone, home address, and birth place in the notes. In Safari on an iPad or
iPhone, opening it shows the contact with "Create New Contact"; the practitioner then imports
it in Healy. Nothing is sent anywhere automatically.

The client's agreement is recorded before the card can be downloaded, because this copies their
details onto the practitioner's device and into a third-party app.
"""
from __future__ import annotations

import json

from flask import flash, redirect, request, url_for

from app import app, iso, login_required, now_utc, open_db

FIELDS = {"first_name": 60, "last_name": 80, "street": 120, "city": 80, "region": 80, "postcode": 20, "country": 80}


def split_name(full_name: str) -> tuple[str, str]:
    """A starting guess: first word as first name, the rest as last name (the practitioner can fix it)."""
    parts = full_name.split()
    if len(parts) < 2:
        return full_name.strip(), ""
    return parts[0], " ".join(parts[1:])


def contact_details(lead) -> dict:
    stored = {}
    if "client_contact" in lead.keys() and lead["client_contact"]:
        try:
            stored = json.loads(lead["client_contact"])
        except ValueError:
            stored = {}
    first, last = split_name(lead["full_name"])
    details = {key: stored.get(key, "") for key in FIELDS}
    details["first_name"] = details["first_name"] or first
    details["last_name"] = details["last_name"] or last
    details["consent_at"] = stored.get("consent_at", "")
    return details


app.jinja_env.globals["healy_contact"] = contact_details


def _clean(value: str | None, limit: int) -> str:
    return " ".join((value or "").split())[:limit]


@app.post("/leads/<int:lead_id>/contact")
@login_required
def save_client_contact(lead_id):
    import workspace
    lead = workspace.scoped_lead(lead_id)
    current = contact_details(lead)
    values = {key: _clean(request.form.get(key), limit) for key, limit in FIELDS.items()}
    if request.form.get("consent") == "yes":
        values["consent_at"] = current["consent_at"] or iso(now_utc())
    else:
        values["consent_at"] = ""
    with open_db() as connection:
        connection.execute("UPDATE leads SET client_contact = ?, updated_at = ? WHERE id = ?",
                           (json.dumps(values), iso(now_utc()), lead["id"]))
    flash("Contact details saved." if values["consent_at"] else
          "Saved. Tick that the client agreed before adding them to your contacts.", "info")
    return redirect(url_for("lead_detail", lead_id=lead["id"]) + "#healy")


def _escape(value: str) -> str:
    return (value.replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;"))


def _fold(line: str) -> str:
    """Fold a content line at 75 octets (vCard / RFC 6350), never splitting a UTF-8 character."""
    out, current = [], b""
    for ch in line:
        encoded = ch.encode()
        if len(current) + len(encoded) > (75 if not out else 74):
            out.append(current.decode())
            current = b""
        current += encoded
    out.append(current.decode())
    return "\r\n ".join(out)


def vcard(lead, details: dict) -> str:
    place = ", ".join(p for p in (lead["birth_city"], lead["birth_region"], lead["birth_country"]) if p)
    lines = [
        "BEGIN:VCARD", "VERSION:3.0",
        f"N:{_escape(details['last_name'])};{_escape(details['first_name'])};;;",
        f"FN:{_escape(' '.join(p for p in (details['first_name'], details['last_name']) if p))}",
        f"BDAY:{lead['birth_date']}",
    ]
    if lead["email"]:
        lines.append(f"EMAIL;TYPE=INTERNET:{_escape(lead['email'])}")
    if lead["phone"]:
        lines.append(f"TEL;TYPE=CELL:{_escape(lead['phone'])}")
    if any(details[key] for key in ("street", "city", "region", "postcode", "country")):
        lines.append("ADR;TYPE=HOME:;;" + ";".join(_escape(details[key]) for key in
                                                 ("street", "city", "region", "postcode", "country")))
    note = f"Birth place: {place}" if place else ""
    lines += [f"NOTE:{_escape(note)}" if note else "", "END:VCARD"]
    return "\r\n".join(_fold(line) for line in lines if line) + "\r\n"


@app.get("/leads/<int:lead_id>/contact.vcf")
@login_required
def client_vcard(lead_id):
    import workspace
    lead = workspace.scoped_lead(lead_id)
    details = contact_details(lead)
    if not details["consent_at"]:
        flash("Tick that the client agreed before adding them to your contacts.", "error")
        return redirect(url_for("lead_detail", lead_id=lead["id"]) + "#healy")
    import numerology  # A-Z spelling of each word (Łukasz -> Lukasz): headers must stay ASCII
    words = f"{details['first_name']} {details['last_name']}".split()
    name = "-".join(filter(None, (numerology.letters_of(word).title() for word in words)))
    return vcard(lead, details), 200, {
        "Content-Type": "text/vcard; charset=utf-8",
        # inline: Safari on iPad/iPhone shows the contact with "Create New Contact"
        "Content-Disposition": f'inline; filename="{name or "client"}.vcf"',
    }

