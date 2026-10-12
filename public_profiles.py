"""Profile photos and public practitioner pages (numer.life/<slug>/about).

Photos are resized in the browser before upload (this server stalls on requests over
~120 KB), then checked and re-encoded here with Pillow: square, at most 512px, JPEG, with
all metadata (EXIF, GPS) removed. They're stored in the database so backups include them.
"""
from __future__ import annotations

import hashlib
import io
import json

from flask import Response, abort, flash, redirect, render_template, request, url_for
from PIL import Image, ImageOps, UnidentifiedImageError

from app import BASE_URL, app, current_user, db, iso, login_required, now_utc
from intake import SLUG_RE, RESERVED_SLUGS
from practitioner_profile import contact_card

AVATAR_SIZE = 512
MAX_PIXELS = 40_000_000  # refuse decompression bombs


def avatar_url(user_id: int) -> str | None:
    row = db().execute("SELECT version FROM avatars WHERE user_id = ?", (user_id,)).fetchone()
    return url_for("avatar", user_id=user_id, v=row["version"]) if row else None


app.jinja_env.globals["avatar_url"] = avatar_url


def process_photo(data: bytes) -> bytes:
    """Validate an uploaded image and return a clean square JPEG. Raises ValueError."""
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ("JPEG", "PNG", "WEBP", "GIF"):
                raise ValueError("Please upload a JPG, PNG or WebP photo.")
            image = ImageOps.exif_transpose(image).convert("RGB")
            image = ImageOps.fit(image, (AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)
            out = io.BytesIO()
            image.save(out, "JPEG", quality=85, optimize=True, progressive=True)
            return out.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("That file isn't a photo we can read. Please try a JPG or PNG.") from exc


@app.post("/settings/photo")
@login_required
def upload_photo():
    user = current_user()
    connection = db()
    if request.form.get("remove") == "yes":
        connection.execute("DELETE FROM avatars WHERE user_id = ?", (user["id"],))
        connection.commit()
        flash("Photo removed.", "info")
        return redirect(url_for("edit_profile"))
    upload = request.files.get("photo")
    if upload is None or not upload.filename:
        flash("Choose a photo first.", "error")
        return redirect(url_for("edit_profile"))
    try:
        image = process_photo(upload.read())
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("edit_profile"))
    version = hashlib.sha256(image).hexdigest()[:12]
    connection.execute(
        "INSERT INTO avatars (user_id, image, version, updated_at) VALUES (?, ?, ?, ?)"
        " ON CONFLICT(user_id) DO UPDATE SET image = excluded.image, version = excluded.version,"
        " updated_at = excluded.updated_at",
        (user["id"], image, version, iso(now_utc())),
    )
    connection.commit()
    flash("Photo updated.", "info")
    return redirect(url_for("edit_profile"))


@app.get("/avatar/<int:user_id>.jpg")
def avatar(user_id):
    row = db().execute(
        "SELECT avatars.image, avatars.version FROM avatars JOIN users ON users.id = avatars.user_id"
        " WHERE avatars.user_id = ? AND users.is_active = 1", (user_id,)
    ).fetchone()
    if row is None:
        abort(404)
    cache = "public, max-age=31536000, immutable" if request.args.get("v") == row["version"] else "public, max-age=300"
    return Response(bytes(row["image"]), mimetype="image/jpeg", headers={"Cache-Control": cache})


# --------------------------------------------------------------------------
# Public practitioner page
# --------------------------------------------------------------------------
def public_practitioner(slug: str):
    if not SLUG_RE.match(slug) or slug in RESERVED_SLUGS:
        return None
    return db().execute(
        "SELECT * FROM users WHERE slug = ? AND role = 'practitioner' AND is_active = 1", (slug,)
    ).fetchone()


@app.get("/<slug>/about")
def practitioner_about(slug):
    row = public_practitioner(slug)
    if row is None:
        abort(404)
    card = contact_card(row)
    report_count = db().execute(
        "SELECT COUNT(*) FROM leads WHERE practitioner_id = ?", (row["id"],)).fetchone()[0]
    person = {
        "@context": "https://schema.org",
        "@type": "ProfilePage",
        "url": f"{BASE_URL}/{slug}/about",
        "mainEntity": {
            "@type": "Person",
            "name": card["name"],
            "jobTitle": card["headline"] or "Numerologist",
            "description": card["bio"] or None,
            "url": f"{BASE_URL}/{slug}/about",
            "sameAs": [s["url"] for s in card["socials"]] + ([card["website"]] if card["website"] else []),
        },
    }
    if avatar_url(row["id"]):
        person["mainEntity"]["image"] = BASE_URL + avatar_url(row["id"])
    person["mainEntity"] = {k: v for k, v in person["mainEntity"].items() if v}
    return render_template("about.html", practitioner=row, card=card, slug=slug, report_count=report_count,
                           person_ld=json.dumps(person), photo=avatar_url(row["id"]))


def sitemap_profiles() -> list[str]:
    rows = db().execute(
        "SELECT slug FROM users WHERE role = 'practitioner' AND is_active = 1 AND slug IS NOT NULL ORDER BY slug"
    ).fetchall()
    return [f"/{row['slug']}/about" for row in rows]

