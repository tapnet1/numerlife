import re

from test_app import numer
from test_client_page import ClientPageTest


class ClientReferralTest(ClientPageTest):
    def test_friend_who_joins_is_tracked(self):
        client_a = self.lead_via_liliana()
        token = self.page_row(client_a["id"])["token"]
        page = numer.app.test_client().get(f"/me/{token}").get_data(as_text=True)
        self.assertIn("Share numer.life with a friend", page)
        self.assertIn("js/share.js", page)
        url = re.search(r'data-share-url="([^"]+)"', page).group(1)
        self.assertIn("/liliana?r=", url)
        path = url.split("://", 1)[1].split("/", 1)[1]
        self.assertIn("No friends yet", page)
        # the friend opens the link in their own browser
        self.client = numer.app.test_client()
        response = self.submit("/" + path, ip="10.2.0.1", full_name="Ana Maria Lopez")
        report_token = response.headers["Location"].rsplit("/", 1)[-1]
        friend = self.leads()[-1]
        self.assertEqual(friend["referred_by"], client_a["id"])
        self.assertEqual(friend["practitioner_id"], client_a["practitioner_id"])
        self.assertNotIn("Ana", numer.app.test_client().get(f"/me/{token}").get_data(as_text=True))  # not unlocked yet
        self.unlock(report_token, ip="10.2.0.1", email="ana@example.com")
        page = numer.app.test_client().get(f"/me/{token}").get_data(as_text=True)
        self.assertIn("Friends who got their report: Ana", page)
        # the practitioner sees who referred whom
        self.client = numer.app.test_client()
        self.login("liliana@example.com")
        self.assertIn("Referred by", self.client.get(f"/leads/{friend['id']}").get_data(as_text=True))
        self.assertIn("Referred 1 friend", self.client.get(f"/leads/{client_a['id']}").get_data(as_text=True))

    def test_unknown_code_is_ignored(self):
        self.client = numer.app.test_client()
        self.submit("/liliana?r=nonexistent1", ip="10.3.0.1")
        self.assertIsNone(self.leads()[-1]["referred_by"])
