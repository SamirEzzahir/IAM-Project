import unittest
from unittest.mock import Mock, call, patch

from pco_logic import parse_spl
from wimtech_assigner import assign_login_to_first_port
from wimtech_checker import check_all_pcos


NOT_FOUND = {
    "status": "NOT_FOUND", "status_label": "Introuvable",
    "pco_exists": False, "message": "absent",
}


class OmsanFallbackTests(unittest.TestCase):
    def setUp(self):
        self.parsed = parse_spl("OMSANFSE-ZO-121.14")
        self.candidates = self.parsed.pco_candidates[:3]
        self.config = {"headless": True, "timeout_seconds": 5}

    @patch("wimtech_checker.close_driver")
    @patch("wimtech_checker.build_driver")
    @patch("wimtech_checker.check_one_pco")
    def test_check_retries_t_alias_only_after_primary_is_missing(
        self, check_one, build_driver, _close_driver
    ):
        check_one.side_effect = [NOT_FOUND, {
            "status": "AVAILABLE", "status_label": "Disponible",
            "pco_exists": True, "free_ports": ["1"], "free_count": 1,
            "message": "libre",
        }]
        driver = build_driver.return_value
        results = []

        check_all_pcos(
            config=self.config, odf=self.parsed.odf, zr=self.parsed.zr,
            candidates=self.candidates, wait_if_paused=lambda: None,
            is_stopped=lambda: False, on_log=lambda *_args: None,
            on_result=lambda index, result: results.append((index, result)),
        )

        self.assertEqual(
            [item.args[-1] for item in check_one.call_args_list],
            ["OMSANFSE-ZO-21.1411", "OMSANFSE-ZO-T.1411"],
        )
        self.assertEqual(results[0][1]["pco"], "OMSANFSE-ZO-T.1411")
        self.assertEqual(results[1][1]["pco"], "OMSANFSE-ZO-T.1411/1")

    @patch("wimtech_assigner.close_driver")
    @patch("wimtech_assigner.build_driver")
    @patch("wimtech_assigner.assign_one_pco")
    def test_assignment_retries_t_alias_and_returns_it(
        self, assign_one, build_driver, _close_driver
    ):
        assign_one.side_effect = [NOT_FOUND, {
            "status": "ASSIGNED", "status_label": "Affecté",
            "pco_exists": True, "selected_port": "1", "message": "ok",
        }]
        results = []

        assigned = assign_login_to_first_port(
            config=self.config, login="LOGIN", odf=self.parsed.odf,
            zr=self.parsed.zr, candidates=self.candidates,
            is_stopped=lambda: False, on_log=lambda *_args: None,
            on_result=lambda index, result: results.append((index, result)),
        )

        self.assertEqual(
            [item.kwargs["pco"] for item in assign_one.call_args_list],
            ["OMSANFSE-ZO-21.1411", "OMSANFSE-ZO-T.1411"],
        )
        self.assertEqual(assigned["pco"], "OMSANFSE-ZO-T.1411")


if __name__ == "__main__":
    unittest.main()
