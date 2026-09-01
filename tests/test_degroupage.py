import unittest
from io import BytesIO

from openpyxl import Workbook

from degroupage import parse_degroupage_workbook


def workbook_bytes(headers, rows):
    workbook = Workbook(); sheet = workbook.active; sheet.append(headers)
    for row in rows: sheet.append(row)
    output = BytesIO(); workbook.save(output)
    return output.getvalue()


class DegroupageTests(unittest.TestCase):
    def test_reads_cmd_login_and_optional_nd(self):
        result = parse_degroupage_workbook(workbook_bytes(
            ["CMD", "Login", "ND"], [["DFOI001", "I10260472", "0600000000"]],
        ))
        self.assertEqual(result["DFOI001"], {"login": "I10260472", "nd": "0600000000"})

    def test_requires_cmd_and_login_headers(self):
        with self.assertRaises(ValueError):
            parse_degroupage_workbook(workbook_bytes(["CMD"], [["DFOI001"]]))


if __name__ == "__main__":
    unittest.main()
