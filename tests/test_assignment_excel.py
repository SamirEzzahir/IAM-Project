import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from openpyxl import Workbook

from assignment_excel import (
    parse_assignment_workbook,
    parse_msan_mapping,
    resolve_msan_spl,
    save_msan_mapping,
)


def workbook_bytes(headers, values):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in values:
        sheet.append(row)
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


class AssignmentExcelTests(unittest.TestCase):
    def test_reads_login_and_spl_batch(self):
        rows = parse_assignment_workbook(workbook_bytes(
            ["Login", "SPL"], [["I10260472", "OFOF-ZO-113.1"]],
        ))
        self.assertEqual(rows[0]["login"], "I10260472")
        self.assertEqual(rows[0]["spl"], "OFOF-ZO-113.1")
        self.assertIsNone(rows[0]["validation_error"])

    def test_saves_and_resolves_msan_mapping(self):
        mappings = parse_msan_mapping(workbook_bytes(
            ["Carte", "Splitter ou SRO"],
            [["GHI-FF-AinKadous:0-0-3-0", "OFAK-ZO-113.1"]],
        ), ".xlsx")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.json"
            save_msan_mapping(path, mappings)
            self.assertEqual(
                resolve_msan_spl(path, "GHI-FF-AinKadous:0-0-3-0"),
                "OFAK-ZO-113.1",
            )


if __name__ == "__main__":
    unittest.main()
