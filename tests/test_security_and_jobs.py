import base64
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

import app
from job_store import JobStore


def basic(username="fb-emm", password="secret"):
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.client = app.app.test_client()

    @patch.dict("os.environ", {"APP_PASSWORD": "secret", "APP_USERNAME": "fb-emm"})
    def test_lan_mode_requires_authentication(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 401)
        self.assertIn("Basic", response.headers["WWW-Authenticate"])

    @patch.dict("os.environ", {"APP_PASSWORD": "secret", "APP_USERNAME": "fb-emm"})
    def test_authenticated_reads_and_protected_writes(self):
        response = self.client.get("/api/health", headers=basic())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")

        rejected = self.client.post(
            "/api/generate-pcos", json={"spl": "OFOF-ZO-113.1"}, headers=basic()
        )
        self.assertEqual(rejected.status_code, 403)

        accepted = self.client.post(
            "/api/generate-pcos",
            json={"spl": "OFOF-ZO-113.1"},
            headers={**basic(), "X-Requested-With": "FB-EMM"},
        )
        self.assertEqual(accepted.status_code, 200)


class JobStoreTests(unittest.TestCase):
    def test_completed_history_is_durable_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory) / "jobs.sqlite3", max_jobs=3)
            for index in range(5):
                job_id = f"job-{index}"
                store.save(job_id, "CHECK", f"2026-01-01T00:00:0{index}Z", {"job_id": job_id})
            self.assertIsNone(store.load("job-0"))
            self.assertEqual(store.load("job-4"), {"job_id": "job-4"})


class JobLifecycleTests(unittest.TestCase):
    def tearDown(self):
        with app.jobs_lock:
            for job in app.jobs.values():
                event = job.get("stop_event")
                if event:
                    event.set()
            app.jobs.clear()

    def test_only_one_selenium_job_can_run(self):
        entered = Event()
        release = Event()

        def blocked_check(**_kwargs):
            entered.set()
            release.wait(2)

        with patch.object(app, "check_all_pcos", side_effect=blocked_check), \
                patch.object(app, "persist_completed_job"), \
                patch.object(app, "persist_available"):
            client = app.app.test_client()
            first = client.post(
                "/api/check/start",
                json={"spl": "OFOF-ZO-113.1"},
                headers={"X-Requested-With": "FB-EMM"},
            )
            self.assertEqual(first.status_code, 200)
            self.assertTrue(entered.wait(1))
            second = client.post(
                "/api/check/start",
                json={"spl": "OFOF-ZO-113.1"},
                headers={"X-Requested-With": "FB-EMM"},
            )
            self.assertEqual(second.status_code, 409)
            release.set()
            with app.jobs_lock:
                thread = next(iter(app.jobs.values()))["thread"]
            thread.join(2)


if __name__ == "__main__":
    unittest.main()
