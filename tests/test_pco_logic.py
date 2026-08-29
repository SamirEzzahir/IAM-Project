import unittest

from pco_logic import group_pco_candidates, parse_spl


class ParseSplTests(unittest.TestCase):
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
