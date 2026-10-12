import datetime as dt

from test_app import AppTestCase, numer

import jobs


class PurgeJobTest(AppTestCase):
    def test_purge_removes_only_old_attempt_records(self):
        old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)).isoformat(timespec="seconds")
        new = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        with numer.open_db() as connection:
            for created in (old, new):
                connection.execute("INSERT INTO login_attempts (ip, email, succeeded, created_at)"
                                   " VALUES ('h', 'e', 0, ?)", (created,))
                connection.execute("INSERT INTO form_attempts (ip_hash, kind, created_at)"
                                   " VALUES ('h', 'submit', ?)", (created,))
        self.assertEqual(jobs.purge_attempts(), 2)
        with numer.open_db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM login_attempts").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM form_attempts").fetchone()[0], 1)

    def test_unknown_job_is_rejected(self):
        self.assertEqual(jobs.main(["jobs.py", "nope"]), 2)
