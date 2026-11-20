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


class ReferralRewardTest(ClientPageTest):
    def setUp(self):
        super().setUp()
        self.client_a = self.lead_via_liliana()
        token = self.page_row(self.client_a["id"])["token"]
        self.me = f"/me/{token}"
        page = numer.app.test_client().get(self.me).get_data(as_text=True)
        self.ref_path = "/" + re.search(r'data-share-url="([^"]+)"', page).group(1).split("://", 1)[1].split("/", 1)[1]

    def friend(self, ip, email, unlock=True):
        self.client = numer.app.test_client()
        response = self.submit(self.ref_path, ip=ip)
        if unlock:
            self.unlock(response.headers["Location"].rsplit("/", 1)[-1], ip=ip, email=email)
        return self.leads()[-1]

    def start_calendar(self):
        visitor = numer.app.test_client()
        html = visitor.get(self.me).get_data(as_text=True)
        visitor.post(f"{self.me}/calendar", data={"csrf_token": numer_token(html)})
        return visitor

    def access(self):
        with numer.app.app_context():
            return numer.calendar_access.access_until(self.calendar())

    def test_each_friend_who_requests_a_report_adds_a_month(self):
        visitor = self.start_calendar()
        before = self.access()
        self.friend("10.4.0.1", "ana@example.com")
        self.friend("10.4.0.2", "luis@example.com")
        after = self.access()
        self.assertGreaterEqual((after - before).days, 59)
        self.assertLessEqual((after - before).days, 62)
        self.assertIn("2 free month", visitor.get(self.me).get_data(as_text=True))

    def test_not_counted(self):
        self.start_calendar()
        before = self.access()
        self.friend("10.5.0.1", None, unlock=False)                          # never asked for the report
        self.friend("10.5.0.2", "john@example.com")                           # the referrer's own email
        self.friend("10.5.0.3", "ana@example.com")
        self.friend("10.5.0.4", "ana@example.com")                            # same person again
        self.assertLessEqual((self.access() - before).days, 31)

    def test_months_wait_for_the_calendar(self):
        self.friend("10.6.0.1", "ana@example.com")
        self.assertIsNone(self.calendar())
        visitor = self.start_calendar()
        cal = self.calendar()
        visitor.get(f"/c/{cal['token']}")
        with numer.app.app_context():
            until = numer.calendar_access.access_until(self.calendar())
        created = numer.calendar_access.dt.date.fromisoformat(cal["created_at"][:10])
        self.assertGreaterEqual((until - created).days, 58)


def numer_token(html):
    from test_app import token_from
    return token_from(html)
