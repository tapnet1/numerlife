import json

from test_app import PASSWORD, numer, token_from
from test_intake import IntakeTestCase

import practitioner_profile as pp


class ProfileTestCase(IntakeTestCase):
    def save(self, **fields):
        token = token_from(self.client.get("/settings/profile").get_data(as_text=True))
        data = {"display_name": "Liliana", "slug": "liliana", "csrf_token": token}
        data.update(fields)
        return self.client.post("/settings/profile", data=data)

    def profile(self):
        with numer.open_db() as connection:
            row = connection.execute("SELECT display_name, slug, profile FROM users WHERE id = ?",
                                     (self.practitioner_id,)).fetchone()
        return row["display_name"], row["slug"], json.loads(row["profile"])


class ProfileTest(ProfileTestCase):
    def test_profile_requires_sign_in(self):
        for path in ("/settings/profile", "/settings/password", "/share/qr.svg"):
            self.assertEqual(self.client.get(path).status_code, 302, path)

    def test_saves_contact_details_and_normalises_social_handles(self):
        self.login("liliana@example.com")
        response = self.save(headline="Certified numerologist", booking_url="calendly.com/liliana",
                             phone="+1 780 555 0101", website="https://liliana.example.com",
                             instagram="@liliana.numbers", tiktok="liliananum", facebook="https://facebook.com/liliana",
                             youtube="youtube.com/@liliana")
        self.assertEqual(response.status_code, 302)
        _, _, profile = self.profile()
        self.assertEqual(profile["booking_url"], "https://calendly.com/liliana")
        self.assertEqual(profile["instagram"], "https://www.instagram.com/liliana.numbers")
        self.assertEqual(profile["tiktok"], "https://www.tiktok.com/@liliananum")
        self.assertEqual(profile["facebook"], "https://facebook.com/liliana")
        self.assertEqual(profile["youtube"], "https://youtube.com/@liliana")

    def test_rejects_unsafe_or_wrong_links(self):
        self.login("liliana@example.com")
        response = self.save(website="javascript:alert(1)", booking_url="http://plain.example.com",
                             instagram="https://evil.example.com/x", phone="call me", contact_email="nope")
        self.assertEqual(response.status_code, 400)
        html = response.get_data(as_text=True)
        for text in ("Website:", "Booking link:", "Instagram:", "Phone:", "Contact email:"):
            self.assertIn(text, html)
        _, _, profile = self.profile()
        self.assertEqual(profile, {})

    def test_change_link_name_and_reject_taken_or_reserved(self):
        numer.create_user("maria@example.com", PASSWORD, "practitioner", "Maria", slug="maria")
        self.login("liliana@example.com")
        self.assertEqual(self.save(slug="maria").status_code, 400)
        self.assertIn("reserved", self.save(slug="admin").get_data(as_text=True))
        self.assertEqual(self.save(slug="liliana-numbers").status_code, 302)
        self.assertEqual(self.profile()[1], "liliana-numbers")
        self.assertEqual(self.client.get("/liliana").status_code, 302)          # old link falls back home
        self.assertIn(b"offered by <strong>Liliana</strong>", self.client.get("/liliana-numbers").data)

    def test_report_shows_the_practitioners_contact_card(self):
        self.login("liliana@example.com")
        self.save(headline="Life path readings", booking_url="https://cal.com/liliana", phone="780-555-0101",
                  instagram="liliana")
        self.client.post("/logout", data={"csrf_token": token_from(self.client.get("/dashboard").get_data(as_text=True))})
        token = self.submit(path="/liliana").headers["Location"].rsplit("/", 1)[-1]
        html = self.client.get(f"/r/{token}").get_data(as_text=True)
        for text in ("Life path readings", 'href="https://cal.com/liliana"', "Book a reading",
                     "tel:780-555-0101", "https://www.instagram.com/liliana", "mailto:liliana@example.com"):
            self.assertIn(text, html)

    def test_only_filled_fields_appear(self):
        card = pp.contact_card({"display_name": "Maria", "email": "m@example.com", "profile": "{}"})
        self.assertEqual(card["socials"], [])
        self.assertEqual(card["email"], "m@example.com")
        self.assertEqual(card["booking_url"], "")


class PasswordTest(ProfileTestCase):
    def change(self, current, new, confirm=None):
        token = token_from(self.client.get("/settings/password").get_data(as_text=True))
        return self.client.post("/settings/password", data={
            "csrf_token": token, "current_password": current, "new_password": new,
            "confirm_password": new if confirm is None else confirm})

    def test_change_password(self):
        self.login("liliana@example.com")
        self.assertEqual(self.change(PASSWORD, "a brand new passphrase").status_code, 302)
        self.client.post("/logout", data={"csrf_token": token_from(self.client.get("/dashboard").get_data(as_text=True))})
        self.assertEqual(self.login("liliana@example.com", "a brand new passphrase").status_code, 302)

    def test_wrong_current_short_or_mismatched(self):
        self.login("liliana@example.com")
        self.assertIn("incorrect", self.change("wrong password!!", "a brand new passphrase").get_data(as_text=True))
        self.assertIn("at least 12", self.change(PASSWORD, "short").get_data(as_text=True))
        self.assertIn("match.", self.change(PASSWORD, "a brand new passphrase", "something else!!").get_data(as_text=True))


class QrTest(ProfileTestCase):
    def test_qr_codes_encode_the_personal_link(self):
        self.login("liliana@example.com")
        svg = self.client.get("/share/qr.svg")
        self.assertEqual(svg.mimetype, "image/svg+xml")
        self.assertIn(b"<svg", svg.data)
        png = self.client.get("/share/qr.png?download=1")
        self.assertEqual(png.mimetype, "image/png")
        self.assertTrue(png.data.startswith(b"\x89PNG"))
        self.assertIn('filename="numerlife-liliana-qr.png"', png.headers["Content-Disposition"])
        self.assertEqual(self.client.get("/share/qr.gif").status_code, 404)

    def test_dashboard_offers_qr_and_profile_nudge(self):
        self.login("liliana@example.com")
        html = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("/share/qr.svg", html)
        self.assertIn("add your booking link", html)
        self.save(booking_url="https://cal.com/liliana")
        self.assertNotIn("add your booking link", self.client.get("/dashboard").get_data(as_text=True))
