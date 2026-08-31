import csv
import io
import unittest
from threading import Event

import app


class AppExportTests(unittest.TestCase):
    def test_sidebar_uses_real_links_as_navigation_fallback(self):
        response = app.app.test_client().get("/")
        html = response.get_data(as_text=True)
        self.assertIn('href="#tab-config" data-tab="config"', html)
        self.assertIn('href="#tab-available" data-tab="available"', html)
        self.assertIn("app.js?v=20260831-3", html)

    def test_available_csv_writes_one_line_per_free_brin(self):
        job_id = "csv-test"
        app.jobs[job_id] = {
            "job_id": job_id, "kind": "CHECK", "spl": "OFOF-ZO-113.1",
            "odf": "OFOF", "zr": "OFOF-ZO", "status": "COMPLETED",
            "total": 1, "completed_count": 1, "results": [{
                "status": "AVAILABLE", "pco": "OFOF-ZO-1111",
                "free_ports": ["2", "4"], "checked_at": "2026-01-01",
            }], "logs": [], "run_event": Event(), "stop_event": Event(),
            "thread": None, "output_path": None,
        }
        try:
            response = app.app.test_client().get(f"/api/check/{job_id}/available.csv")
            rows = list(csv.reader(io.StringIO(response.data.decode("utf-8-sig")), delimiter=";"))
            self.assertEqual(rows[1][3:5], ["OFOF-ZO-1111", "2"])
            self.assertEqual(rows[2][3:5], ["OFOF-ZO-1111", "4"])
        finally:
            app.jobs.pop(job_id, None)


if __name__ == "__main__":
    unittest.main()
