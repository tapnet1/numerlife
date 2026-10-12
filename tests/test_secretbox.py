import base64
import os
import unittest
from unittest import mock

import secretbox

KEY = base64.b64encode(b"k" * 32).decode()


class SecretBoxTest(unittest.TestCase):
    def test_round_trip_and_randomized(self):
        with mock.patch.dict(os.environ, {"NUMER_CREDENTIAL_KEY": KEY}):
            first, second = secretbox.encrypt("sk-test-123"), secretbox.encrypt("sk-test-123")
            self.assertNotEqual(first, second)
            self.assertNotIn("sk-test-123", first)
            self.assertEqual(secretbox.decrypt(first), "sk-test-123")

    def test_missing_or_bad_key(self):
        for value in ("", "not base64!!", base64.b64encode(b"short").decode()):
            with mock.patch.dict(os.environ, {"NUMER_CREDENTIAL_KEY": value}):
                with self.assertRaises(secretbox.CredentialKeyError) as ctx:
                    secretbox.check_key()
                if value:
                    self.assertNotIn(value, str(ctx.exception))

    def test_tampering_is_detected(self):
        with mock.patch.dict(os.environ, {"NUMER_CREDENTIAL_KEY": KEY}):
            token = secretbox.encrypt("secret")
            tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
            with self.assertRaises(Exception):
                secretbox.decrypt(tampered)


if __name__ == "__main__":
    unittest.main()
