from test_app import PASSWORD, numer, token_from
from test_intake import IntakeTestCase

import healy_contact


class HealyContactTest(IntakeTestCase):
    def setUp(self):
        super().setUp()
        token = self.submit(path="/liliana", full_name="María José García López", birth_date="1974-06-19",
                            birth_city="Bogotá", birth_region="", birth_country="Colombia").headers["Location"]
        self.unlock(token.rsplit("/", 1)[-1], email="maria@example.com", phone="+1 780 555 0101")
        self.lead_id = self.leads()[0]["id"]

    def save(self, **fields):
        page = self.client.get(f"/leads/{self.lead_id}").get_data(as_text=True)
        data = {"csrf_token": token_from(page), "first_name": "María José", "last_name": "García López",
                "street": "123 Main St, Unit 4", "city": "Edmonton", "region": "AB", "postcode": "T5J 0A1",
                "country": "Canada"}
        data.update(fields)
        return self.client.post(f"/leads/{self.lead_id}/contact", data=data)

    def test_card_needs_consent_then_has_the_clients_details(self):
        self.login("liliana@example.com")
        page = self.client.get(f"/leads/{self.lead_id}").get_data(as_text=True)
        self.assertIn("Healy contact", page)
        self.assertIn('value="María"', page)  # first guess: first word
        self.save()
        response = self.client.get(f"/leads/{self.lead_id}/contact.vcf")
        self.assertEqual(response.status_code, 302)  # no consent yet
        self.save(consent="yes")
        self.assertIn("Add to Contacts", self.client.get(f"/leads/{self.lead_id}").get_data(as_text=True))
        response = self.client.get(f"/leads/{self.lead_id}/contact.vcf")
        self.assertEqual(response.headers["Content-Type"], "text/vcard; charset=utf-8")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        card = response.get_data(as_text=True)
        for line in ("BEGIN:VCARD", "N:García López;María José;;;", "FN:María José García López",
                     "BDAY:1974-06-19", "EMAIL;TYPE=INTERNET:maria@example.com", "TEL;TYPE=CELL:+1 780 555 0101",
                     "ADR;TYPE=HOME:;;123 Main St\\, Unit 4;Edmonton;AB;T5J 0A1;Canada",
                     "NOTE:Birth place: Bogotá\\, Colombia", "END:VCARD"):
            self.assertIn(line, card)
        self.save()  # unticking withdraws consent
        self.assertEqual(self.client.get(f"/leads/{self.lead_id}/contact.vcf").status_code, 302)

    def test_names_beyond_latin_1_still_download(self):
        self.login("liliana@example.com")
        self.save(first_name="Łukasz", last_name="Żółć", consent="yes")
        response = self.client.get(f"/leads/{self.lead_id}/contact.vcf")
        self.assertEqual(response.status_code, 200)
        self.assertIn('filename="Lukasz-Zolc.vcf"', response.headers["Content-Disposition"])
        response.headers["Content-Disposition"].encode("latin-1")
        self.assertIn("N:Żółć;Łukasz;;;", response.get_data(as_text=True))

    def test_other_practitioners_cannot_get_the_card(self):
        self.login("liliana@example.com")
        self.save(consent="yes")
        self.client.post("/logout")
        self.client = numer.app.test_client()
        numer.create_user("other@example.com", PASSWORD, "practitioner", "Other", slug="other")
        self.login("other@example.com")
        self.assertEqual(self.client.get(f"/leads/{self.lead_id}/contact.vcf").status_code, 404)

    def test_long_lines_are_folded_without_splitting_characters(self):
        lead = {"birth_city": "ñ" * 60, "birth_region": "", "birth_country": "", "birth_date": "1990-01-01",
                "email": "", "phone": ""}
        card = healy_contact.vcard(lead, {"first_name": "A", "last_name": "B", "street": "", "city": "",
                                          "region": "", "postcode": "", "country": ""})
        for line in card.split("\r\n"):
            self.assertLessEqual(len(line.encode()), 75)
        self.assertIn("ñ" * 30, card.replace("\r\n ", ""))
