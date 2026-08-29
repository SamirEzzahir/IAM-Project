import unittest

from wimtech_parser import (
    base_result_confirms_existence,
    extract_available_fibre_ports,
    is_active_fo_cable,
    is_equipment_missing,
    parse_fibre_label,
    usable_fibre_port,
)


class WimtechParserTests(unittest.TestCase):
    def test_accepts_fo4_and_fo8_active_cables(self):
        self.assertTrue(is_active_fo_cable("(FO4-Active), CLFI741050"))
        self.assertTrue(is_active_fo_cable("(FO8-Active), CLFI000001"))
        self.assertFalse(is_active_fo_cable("(FO4-Inactive), CLFI741050"))

    def test_collects_libre_and_en_cours_decon_only(self):
        labels = [
            "1 (FIBRE-Active), Z20SKB0P (En service)",
            "2 (FIBRE-En cours), 79MOSTAPHA (En cours)",
            "3 (FIBRE-Libre),",
            "4 (FIBRE-Active), ELGHEZAOUI (En cours decon)",
            "8 (FIBRE-Libre),",
        ]
        self.assertEqual(extract_available_fibre_ports(labels), ["3", "4", "8"])

    def test_first_usable_port_parser_matches_assignment_policy(self):
        self.assertEqual(usable_fibre_port("3 (FIBRE-Libre),"), "3")
        self.assertEqual(
            usable_fibre_port("4 (FIBRE-Active), CLIENT (En cours decon)"),
            "4",
        )
        self.assertIsNone(
            usable_fibre_port("1 (FIBRE-Active), CLIENT (En service)")
        )
        self.assertIsNone(
            usable_fibre_port("2 (FIBRE-En cours), CLIENT (En cours)")
        )

    def test_extracts_previous_login_before_bulk_mutation(self):
        details = parse_fibre_label(
            "1 (FIBRE-Active), Z20SKB0P (En service)"
        )
        self.assertEqual(details["port"], "1")
        self.assertEqual(details["fibre_state"], "Active")
        self.assertEqual(details["current_login"], "Z20SKB0P")
        self.assertEqual(details["service_state"], "En service")

        libre = parse_fibre_label("3 (FIBRE-Libre),")
        self.assertEqual(libre["port"], "3")
        self.assertIsNone(libre["current_login"])

    def test_detects_missing_equipment_error(self):
        self.assertTrue(
            is_equipment_missing(
                "Pas d'équipement installé au niveau de cette géolocalisation"
            )
        )

    def test_split_forms_are_skipped_only_when_base_existence_is_confirmed(self):
        self.assertTrue(base_result_confirms_existence({"pco_exists": True}))
        self.assertFalse(base_result_confirms_existence({"pco_exists": False}))
        self.assertFalse(base_result_confirms_existence({"status": "ERROR"}))


if __name__ == "__main__":
    unittest.main()
