import threading

from werkzeug.test import Client

from test_app import PASSWORD, numer, token_from
from test_intake import IntakeTestCase, form_data


class RoundRobinTest(IntakeTestCase):
    def setUp(self):
        super().setUp()
        self.maria = numer.create_user("maria@example.com", PASSWORD, "practitioner", "Maria", slug="maria")
        self.sam = numer.create_user("sam@example.com", PASSWORD, "practitioner", "Sam", slug="sam")

    def visitor_submits(self, n):
        ip = f"10.8.0.{n}"
        client = numer.app.test_client()
        page = client.get("/", environ_base={"REMOTE_ADDR": ip})
        data = form_data(csrf_token=token_from(page.get_data(as_text=True)))
        client.post("/report", data=data, environ_base={"REMOTE_ADDR": ip})
        return client

    def owners(self):
        with numer.open_db() as connection:
            return [r[0] for r in connection.execute("SELECT practitioner_id FROM leads ORDER BY id")]

    def test_visitors_without_a_link_are_shared_in_turn(self):
        for n in range(6):
            self.visitor_submits(n)
        owners = self.owners()
        self.assertEqual(sorted(owners[:3]), sorted([self.practitioner_id, self.maria, self.sam]))
        self.assertEqual(owners[3:], owners[:3])  # same order on the next round

    def test_link_visitors_do_not_use_up_a_turn(self):
        self.submit(path="/maria")
        for n in range(3):
            self.visitor_submits(n)
        owners = self.owners()
        self.assertEqual(owners[0], self.maria)
        # Maria's own link visitor doesn't cost her a shared visitor: all three still get one.
        self.assertEqual(sorted(owners[1:]), sorted([self.practitioner_id, self.maria, self.sam]))

    def test_opted_out_inactive_and_admins_are_skipped(self):
        with numer.open_db() as connection:
            connection.execute("UPDATE users SET round_robin = 0 WHERE id = ?", (self.maria,))
            connection.execute("UPDATE users SET is_active = 0 WHERE id = ?", (self.sam,))
        for n in range(3):
            self.visitor_submits(n)
        self.assertEqual(set(self.owners()), {self.practitioner_id})

    def test_nobody_eligible_leaves_lead_for_admins(self):
        with numer.open_db() as connection:
            connection.execute("UPDATE users SET round_robin = 0")
        self.visitor_submits(1)
        with numer.open_db() as connection:
            lead = connection.execute("SELECT practitioner_id, source FROM leads").fetchone()
        self.assertEqual((lead["practitioner_id"], lead["source"]), (None, "none"))

    def test_assigned_practitioner_is_named_in_consent_and_shares(self):
        client = self.visitor_submits(1)
        with numer.open_db() as connection:
            lead = connection.execute("SELECT token, practitioner_id FROM leads").fetchone()
            name, slug = connection.execute("SELECT display_name, slug FROM users WHERE id = ?",
                                            (lead["practitioner_id"],)).fetchone()
        html = client.get(f"/r/{lead['token']}").get_data(as_text=True)
        self.assertIn(f"share my details and report with {name}", html)
        self.assertIn(f'data-share-url="http://localhost/{slug}"', html)
        # the same visitor's next report stays with the same practitioner
        page = client.get("/", environ_base={"REMOTE_ADDR": "10.8.0.1"})
        self.assertIn(f"offered by <strong>{name}</strong>", page.get_data(as_text=True))

    def test_parallel_visitors_get_different_practitioners(self):
        results = []
        def worker(n):
            self.visitor_submits(n)
        threads = [threading.Thread(target=worker, args=(n,)) for n in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(self.owners()), sorted([self.practitioner_id, self.maria, self.sam]))

    def test_practitioner_can_opt_out_in_profile(self):
        self.login("liliana@example.com")
        token = token_from(self.client.get("/settings/profile").get_data(as_text=True))
        self.client.post("/settings/profile", data={"csrf_token": token, "display_name": "Liliana", "slug": "liliana"})
        with numer.open_db() as connection:
            self.assertEqual(connection.execute("SELECT round_robin FROM users WHERE id = ?",
                                                (self.practitioner_id,)).fetchone()[0], 0)
        self.client.post("/settings/profile", data={"csrf_token": token, "display_name": "Liliana",
                                                    "slug": "liliana", "round_robin": "yes"})
        with numer.open_db() as connection:
            self.assertEqual(connection.execute("SELECT round_robin FROM users WHERE id = ?",
                                                (self.practitioner_id,)).fetchone()[0], 1)

    def test_lead_shows_how_it_arrived(self):
        self.visitor_submits(1)
        self.login("owner@example.com")
        with numer.open_db() as connection:
            lead_id = connection.execute("SELECT id FROM leads").fetchone()[0]
        self.assertIn("shared visitor", self.client.get(f"/leads/{lead_id}").get_data(as_text=True))
