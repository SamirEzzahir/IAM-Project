import unittest

from pco_catalog import build_pco_catalog


def result(pco, exists, ports=(), status=None):
    return {
        "pco": pco,
        "pco_exists": exists,
        "free_ports": list(ports),
        "status": status or ("AVAILABLE" if ports else "NOT_FOUND"),
        "checked_at": "2026-09-01T00:00:00Z",
    }


class PcoCatalogTests(unittest.TestCase):
    def test_existing_base_keeps_its_name_and_ports_one_to_eight(self):
        catalog = build_pco_catalog([
            result("ODF-ZO-311", True, ["1", "3", "5", "7"]),
            result("ODF-ZO-311/1", None, status="SKIPPED"),
            result("ODF-ZO-311/2", None, status="SKIPPED"),
        ])
        self.assertEqual(
            [(row["pco"], row["brin"], row["status"]) for row in catalog],
            [
                ("ODF-ZO-311", "1", "AVAILABLE"),
                ("ODF-ZO-311", "3", "AVAILABLE"),
                ("ODF-ZO-311", "5", "AVAILABLE"),
                ("ODF-ZO-311", "7", "AVAILABLE"),
            ],
        )

    def test_split_one_exists_and_split_two_is_not_created(self):
        catalog = build_pco_catalog([
            result("ODF-ZO-311", False),
            result("ODF-ZO-311/1", True, ["1", "3"]),
            result("ODF-ZO-311/2", False),
        ])
        self.assertEqual(
            [(row["pco"], row["brin"], row["status"]) for row in catalog],
            [
                ("ODF-ZO-311/1", "1", "AVAILABLE"),
                ("ODF-ZO-311/1", "3", "AVAILABLE"),
                ("ODF-ZO-311/2", None, "NOT_CREATED"),
            ],
        )

    def test_split_two_exists_and_split_one_is_not_created(self):
        catalog = build_pco_catalog([
            result("ODF-ZO-311", False),
            result("ODF-ZO-311/1", False),
            result("ODF-ZO-311/2", True, ["2"]),
        ])
        self.assertEqual(
            [(row["pco"], row["brin"], row["status"]) for row in catalog],
            [
                ("ODF-ZO-311/1", None, "NOT_CREATED"),
                ("ODF-ZO-311/2", "2", "AVAILABLE"),
            ],
        )

    def test_all_three_missing_saves_only_base_as_not_created(self):
        catalog = build_pco_catalog([
            result("ODF-ZO-311", False),
            result("ODF-ZO-311/1", False),
            result("ODF-ZO-311/2", False),
        ])
        self.assertEqual(len(catalog), 1)
        self.assertEqual(catalog[0]["pco"], "ODF-ZO-311")
        self.assertEqual(catalog[0]["status"], "NOT_CREATED")


if __name__ == "__main__":
    unittest.main()
