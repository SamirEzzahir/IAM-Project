import unittest

from pco_logic import alternate_omsan_pco, group_pco_candidates, parse_spl


class ParseSplTests(unittest.TestCase):
    def test_omsan_pcos_use_baie_card_prefix_and_have_t_alias(self):
        result = parse_spl("OMSANFSE-ZO-121.14")
        self.assertEqual(
            result.pco_bases,
            ["21.1411", "21.1412", "21.1421", "21.1422"],
        )
        self.assertEqual(result.pco_candidates[0], "OMSANFSE-ZO-21.1411")
        self.assertEqual(result.pco_candidates[-1], "OMSANFSE-ZO-21.1422/2")
        self.assertEqual(
            alternate_omsan_pco(result.pco_candidates[0]),
            "OMSANFSE-ZO-T.1411",
        )
        self.assertIsNone(alternate_omsan_pco("OFBT03-ZO-311"))

    def test_four_digit_equipment_alias_generates_the_same_pcos(self):
        regular = parse_spl("OFBT03-ZO-111.3")
        alternate = parse_spl("OFBT03-ZO-1111.3")

        self.assertEqual(alternate.spl, "OFBT03-ZO-1111.3")
        self.assertEqual(
            (alternate.chassis, alternate.baie, alternate.card, alternate.port),
            (1, 1, 1, 3),
        )
        self.assertEqual(alternate.pco_candidates, regular.pco_candidates)
        self.assertEqual(
            alternate.pco_candidates,
            [
                "OFBT03-ZO-311", "OFBT03-ZO-311/1", "OFBT03-ZO-311/2",
                "OFBT03-ZO-312", "OFBT03-ZO-312/1", "OFBT03-ZO-312/2",
                "OFBT03-ZO-321", "OFBT03-ZO-321/1", "OFBT03-ZO-321/2",
                "OFBT03-ZO-322", "OFBT03-ZO-322/1", "OFBT03-ZO-322/2",
            ],
        )

    def test_card_one_has_no_prefix(self):
        result = parse_spl("O2C1-ZO-111.5")
        self.assertEqual(result.odf, "O2C1")
        self.assertEqual(result.zr, "O2C1-ZO")
        self.assertEqual(result.pco_bases, ["511", "512", "521", "522"])
        self.assertEqual(len(result.pco_candidates), 12)
        self.assertEqual(result.pco_candidates[0], "O2C1-ZO-511")
        self.assertEqual(result.pco_candidates[2], "O2C1-ZO-511/2")

    def test_card_three_is_prefixed(self):
        result = parse_spl("OFAD33-ZO-113.16")
        self.assertEqual(result.pco_bases, ["31611", "31612", "31621", "31622"])
        self.assertEqual(result.pco_candidates[-1], "OFAD33-ZO-31622/2")

    def test_port_range(self):
        with self.assertRaises(ValueError):
            parse_spl("OFAD33-ZO-113.17")

    def test_candidates_are_grouped_base_then_split_forms(self):
        result = parse_spl("OFAD33-ZO-113.16")
        groups = group_pco_candidates(result.pco_candidates)
        self.assertEqual(len(groups), 4)
        self.assertEqual(
            groups[0],
            (
                "OFAD33-ZO-31611",
                "OFAD33-ZO-31611/1",
                "OFAD33-ZO-31611/2",
            ),
        )


if __name__ == "__main__":
    unittest.main()
