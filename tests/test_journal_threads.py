from test_app import numer, token_from
from test_calendar_journal import JournalTest


class JournalThreadTest(JournalTest):
    def replies(self):
        with numer.open_db() as connection:
            return connection.execute("SELECT * FROM journal_replies ORDER BY id").fetchall()

    def test_practitioner_and_client_talk_on_a_note(self):
        visitor, _ = self.write()
        note = self.entries()[0]
        page = self.client.get(f"/leads/{self.lead['id']}").get_data(as_text=True)
        self.assertIn("Reply to", page)
        self.client.post(f"/leads/{self.lead['id']}/journal/{note['id']}/reply",
                         data={"csrf_token": token_from(page), "body": "Qué bonito, cuéntame más."})
        with numer.open_db() as connection:
            mail = connection.execute("SELECT * FROM email_outbox WHERE kind = 'journal_reply'").fetchone()
        self.assertEqual(mail["to_addr"], self.lead["email"])
        self.assertIn("respondió", mail["subject"])
        self.assertIn(f"#d{self.today.day}", mail["body_text"])
        cal_page = visitor.get(f"/c/{self.cal['token']}/{self.key}").get_data(as_text=True)
        self.assertIn("Qué bonito, cuéntame más.", cal_page)
        visitor.post(f"/c/{self.cal['token']}/notes/{note['id']}/reply",
                     data={"csrf_token": token_from(cal_page), "body": "Claro, fue increíble.", "tz": "America/Edmonton"})
        rows = self.replies()
        self.assertEqual([r["author"] for r in rows], ["staff", "client"])
        self.assertIsNone(self.entries()[0]["seen_at"])                  # shows as New again
        with numer.open_db() as connection:
            alert = connection.execute("SELECT * FROM email_outbox WHERE kind = 'feedback_alert'"
                                       " ORDER BY id DESC").fetchone()
        self.assertIn("Journal reply from", alert["subject"])
        lead_page = self.client.get(f"/leads/{self.lead['id']}").get_data(as_text=True)
        self.assertIn("Claro, fue increíble.", lead_page)

    def test_cannot_reply_to_another_clients_note(self):
        self.write()
        note = self.entries()[0]
        other = self.lead_via_liliana()
        self.start(other["id"], kind="numerology", lang="es")
        with numer.open_db() as connection:
            other_cal = connection.execute("SELECT * FROM calendars WHERE lead_id = ?", (other["id"],)).fetchone()
        visitor = numer.app.test_client()
        page = visitor.get(f"/c/{other_cal['token']}").get_data(as_text=True)
        response = visitor.post(f"/c/{other_cal['token']}/notes/{note['id']}/reply",
                                data={"csrf_token": token_from(page), "body": "hola"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.replies(), [])

    def test_journal_book_and_prompt(self):
        visitor, _ = self.write(body="Primera nota del diario.")
        cal_page = visitor.get(f"/c/{self.cal['token']}/{self.key}").get_data(as_text=True)
        self.assertIn("Hoy es un día", cal_page)
        self.assertIn("1 notas hasta ahora", cal_page)
        book = visitor.get(f"/c/{self.cal['token']}/journal")
        html = book.get_data(as_text=True)
        self.assertEqual(book.headers["Cache-Control"], "no-store")
        self.assertIn("Primera nota del diario.", html)
        self.assertIn("Imprimir o guardar como PDF", html)

    def test_deleting_a_note_removes_its_replies(self):
        visitor, _ = self.write()
        note = self.entries()[0]
        page = visitor.get(f"/c/{self.cal['token']}/{self.key}").get_data(as_text=True)
        visitor.post(f"/c/{self.cal['token']}/notes/{note['id']}/reply", data={"csrf_token": token_from(page), "body": "x"})
        visitor.post(f"/c/{self.cal['token']}/notes/{note['id']}/delete", data={"csrf_token": token_from(page)})
        self.assertEqual(self.replies(), [])
