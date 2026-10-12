import json
import os
from unittest import mock

from test_app import PASSWORD, numer, token_from
from test_intake import IntakeTestCase

import notify
import webpush

VAPID = webpush.generate_private_key()


def fake_subscription():
    """A real P-256 key pair and auth secret, like a browser would send."""
    from cryptography.hazmat.primitives.asymmetric import ec
    key = ec.generate_private_key(ec.SECP256R1())
    return {"endpoint": "https://fcm.googleapis.com/fcm/send/" + os.urandom(8).hex(),
            "keys": {"p256dh": webpush.b64u(webpush._point(key.public_key())),
                     "auth": webpush.b64u(os.urandom(16))}}


class NotifyTestCase(IntakeTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.dict(os.environ, {"NUMER_VAPID_PRIVATE_KEY": VAPID})
        patcher.start()
        self.addCleanup(patcher.stop)

    def subscribe(self, subscription=None):
        csrf = token_from(self.client.get("/dashboard").get_data(as_text=True))
        return self.client.post("/push/subscribe", json=subscription or fake_subscription(),
                                headers={"X-CSRF-Token": csrf})

    def outbox(self):
        with numer.open_db() as connection:
            return connection.execute("SELECT * FROM push_outbox ORDER BY id").fetchall()


class PwaTest(NotifyTestCase):
    def test_manifest_and_service_worker(self):
        manifest = self.client.get("/manifest.webmanifest")
        self.assertEqual(manifest.mimetype, "application/manifest+json")
        data = manifest.get_json(force=True)
        self.assertEqual(data["display"], "standalone")
        self.assertEqual(data["scope"], "/")
        self.assertTrue(any(icon["sizes"] == "512x512" for icon in data["icons"]))
        sw = self.client.get("/sw.js")
        self.assertEqual(sw.status_code, 200)
        self.assertEqual(sw.mimetype, "application/javascript")
        self.assertEqual(sw.headers["Service-Worker-Allowed"], "/")
        self.assertIn(b"showNotification", sw.data)
        sw.close()
        self.assertIn(b'rel="manifest"', self.client.get("/").data)

    def test_dashboard_has_alerts_panel_with_public_key(self):
        self.login("liliana@example.com")
        html = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn(f'data-vapid="{webpush.public_key()}"', html)
        self.assertIn("Turn on notifications", html)
        self.assertNotIn(VAPID, html)


class SubscribeTest(NotifyTestCase):
    def test_subscribe_requires_sign_in_and_csrf(self):
        self.assertEqual(self.client.post("/push/subscribe", json=fake_subscription()).status_code, 400)
        self.login("liliana@example.com")
        self.assertEqual(self.client.post("/push/subscribe", json=fake_subscription()).status_code, 400)
        self.assertEqual(self.subscribe().status_code, 200)

    def test_rejects_unknown_push_services(self):
        self.login("liliana@example.com")
        bad = fake_subscription()
        bad["endpoint"] = "https://attacker.example.com/push"
        self.assertEqual(self.subscribe(bad).status_code, 400)
        bad["endpoint"] = "http://fcm.googleapis.com/fcm/send/x"
        self.assertEqual(self.subscribe(bad).status_code, 400)

    def test_resubscribe_updates_and_unsubscribe_removes(self):
        self.login("liliana@example.com")
        sub = fake_subscription()
        self.subscribe(sub)
        self.subscribe(sub)
        self.assertEqual(notify.connect().execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0], 1)
        csrf = token_from(self.client.get("/dashboard").get_data(as_text=True))
        self.client.post("/push/unsubscribe", json={"endpoint": sub["endpoint"]}, headers={"X-CSRF-Token": csrf})
        with numer.open_db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0], 0)


class QueueAndSendTest(NotifyTestCase):
    def unlock_new_lead(self, path="/liliana"):
        token = self.submit(path=path).headers["Location"].rsplit("/", 1)[-1]
        self.unlock(token)

    def test_unlock_queues_alert_for_the_practitioner(self):
        self.unlock_new_lead()
        rows = self.outbox()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["user_id"], self.practitioner_id)
        message = json.loads(rows[0]["payload"])
        self.assertEqual(message["title"], "New lead: John")
        self.assertIn("Life Path 5", message["body"])
        self.assertTrue(message["url"].startswith("/leads/"))

    def test_leads_without_practitioner_alert_admins(self):
        with numer.open_db() as connection:
            connection.execute("UPDATE users SET round_robin = 0")  # nobody takes shared visitors
        self.unlock_new_lead(path="/")
        self.assertEqual([r["user_id"] for r in self.outbox()], [self.admin_id])

    def test_starting_a_report_does_not_alert(self):
        self.submit(path="/liliana")
        self.assertEqual(self.outbox(), [])

    def test_send_delivers_encrypted_message_with_vapid(self):
        self.login("liliana@example.com")
        self.subscribe()
        self.unlock_new_lead()
        calls = []
        def post(url, body, headers, timeout):
            calls.append((url, body, headers))
            return 201, ""
        stats = notify.send_pending(post=post)
        self.assertEqual(stats["sent"], 1)
        url, body, headers = calls[0]
        self.assertTrue(url.startswith("https://fcm.googleapis.com/"))
        self.assertTrue(headers["Authorization"].startswith("vapid t="))
        self.assertEqual(headers["Content-Encoding"], "aes128gcm")
        self.assertNotIn(b"New lead", body)  # payload is encrypted
        self.assertIsNotNone(self.outbox()[0]["sent_at"])
        self.assertEqual(notify.send_pending(post=post)["sent"], 0)  # never sent twice

    def test_gone_subscriptions_are_removed_and_failures_retried(self):
        self.login("liliana@example.com")
        self.subscribe()
        self.unlock_new_lead()
        stats = notify.send_pending(post=lambda *a: (500, "busy"))
        self.assertEqual(stats["failed"], 1)
        self.assertIsNone(self.outbox()[0]["sent_at"])
        stats = notify.send_pending(post=lambda *a: (410, "gone"))
        self.assertEqual(stats["removed"], 1)
        with numer.open_db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM push_subscriptions").fetchone()[0], 0)

    def test_test_notification(self):
        self.login("liliana@example.com")
        csrf = token_from(self.client.get("/dashboard").get_data(as_text=True))
        self.assertEqual(self.client.post("/push/test", headers={"X-CSRF-Token": csrf}).status_code, 400)
        self.subscribe()
        self.assertEqual(self.client.post("/push/test", headers={"X-CSRF-Token": csrf}).status_code, 200)
        self.assertIn("notifications are on", json.loads(self.outbox()[-1]["payload"])["title"])

    def test_notification_failure_never_breaks_unlock(self):
        with mock.patch.object(notify, "queue_new_lead", side_effect=RuntimeError("boom")):
            token = self.submit(path="/liliana").headers["Location"].rsplit("/", 1)[-1]
            self.assertEqual(self.unlock(token).status_code, 302)


class WebPushCryptoTest(NotifyTestCase):
    def test_encrypted_payload_decrypts_with_the_browser_keys(self):
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        ua_key = ec.generate_private_key(ec.SECP256R1())
        ua_public = webpush._point(ua_key.public_key())
        auth = os.urandom(16)
        body = webpush.encrypt(b'{"hi":1}', webpush.b64u(ua_public), webpush.b64u(auth))
        salt, keylen = body[:16], body[20]
        as_public = body[21:21 + keylen]
        shared = ua_key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
        ikm = webpush._hkdf_expand(webpush._hkdf_extract(auth, shared), b"WebPush: info\x00" + ua_public + as_public, 32)
        prk = webpush._hkdf_extract(salt, ikm)
        cek = webpush._hkdf_expand(prk, b"Content-Encoding: aes128gcm\x00", 16)
        nonce = webpush._hkdf_expand(prk, b"Content-Encoding: nonce\x00", 12)
        plain = AESGCM(cek).decrypt(nonce, body[21 + keylen:], None)
        self.assertEqual(plain, b'{"hi":1}\x02')
