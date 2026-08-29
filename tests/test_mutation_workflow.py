import importlib.util
import sys
import types
import unittest
from unittest.mock import ANY, Mock, call, patch


if importlib.util.find_spec("selenium") is None:
    selenium = types.ModuleType("selenium")
    webdriver = types.ModuleType("selenium.webdriver")
    common = types.ModuleType("selenium.common")
    exceptions = types.ModuleType("selenium.common.exceptions")
    webdriver_common = types.ModuleType("selenium.webdriver.common")
    by_module = types.ModuleType("selenium.webdriver.common.by")
    support = types.ModuleType("selenium.webdriver.support")
    expected_conditions = types.ModuleType(
        "selenium.webdriver.support.expected_conditions"
    )
    ui_module = types.ModuleType("selenium.webdriver.support.ui")

    class SeleniumStubException(Exception):
        pass

    class By:
        ID = "id"
        XPATH = "xpath"
        CSS_SELECTOR = "css selector"
        TAG_NAME = "tag name"

    class WebDriverWait:
        def __init__(self, *_args, **_kwargs):
            pass

        def until(self, condition):
            return condition(None)

    def condition(*_args, **_kwargs):
        return lambda _driver: True

    exceptions.StaleElementReferenceException = SeleniumStubException
    exceptions.TimeoutException = SeleniumStubException
    by_module.By = By
    ui_module.WebDriverWait = WebDriverWait
    expected_conditions.element_to_be_clickable = condition
    expected_conditions.visibility_of_element_located = condition
    expected_conditions.presence_of_element_located = condition
    expected_conditions.staleness_of = condition
    selenium.webdriver = webdriver
    common.exceptions = exceptions
    webdriver.common = webdriver_common
    webdriver_common.by = by_module
    webdriver.support = support
    support.expected_conditions = expected_conditions
    support.ui = ui_module

    sys.modules.update({
        "selenium": selenium,
        "selenium.webdriver": webdriver,
        "selenium.common": common,
        "selenium.common.exceptions": exceptions,
        "selenium.webdriver.common": webdriver_common,
        "selenium.webdriver.common.by": by_module,
        "selenium.webdriver.support": support,
        "selenium.webdriver.support.expected_conditions": expected_conditions,
        "selenium.webdriver.support.ui": ui_module,
    })

from wimtech_checker import (
    delete_old_constitution,
    extract_msan_port_from_equipment_table,
    find_deletable_constitution_checkboxes,
    has_invalid_odf_error,
    has_no_available_fibre_port,
    odf_with_msan,
    submit_pco_location,
)
from wimtech_bulk_mutator import extract_spl_from_constitution


class FakeCheckbox:
    def __init__(self, *, enabled=True, selected=False):
        self.enabled = enabled
        self.selected = selected

    def is_enabled(self):
        return self.enabled

    def is_selected(self):
        return self.selected


class FakeDriver:
    def __init__(self, elements=None):
        self.elements = elements or []

    def find_elements(self, *_args):
        return self.elements


class FakeCell:
    def __init__(self, text="", text_content=None):
        self.text = text
        self.text_content = text if text_content is None else text_content

    def get_attribute(self, name):
        return self.text_content if name == "textContent" else None


class FakeRow:
    def __init__(self, cells):
        self.cells = cells

    def find_elements(self, *_args):
        return self.cells


class FakeTable:
    def __init__(self, rows):
        self.rows = rows

    def find_elements(self, *_args):
        return self.rows


class ImmediateWait:
    def __init__(self, result=None):
        self.result = result if result is not None else object()

    def until(self, _condition):
        return self.result


class MutationWorkflowTests(unittest.TestCase):
    def test_extracts_msan_values_from_text_content_after_empty_transition_row(self):
        empty_row = FakeRow([FakeCell() for _ in range(6)])
        populated_cells = [
            FakeCell(text_content="MSAN2C2"),
            FakeCell(text_content="MHOu-Fe-MourabitineERAC1--2C2"),
            FakeCell(text_content="0"),
            FakeCell(text_content="18"),
            FakeCell(text_content="7"),
            FakeCell(text_content="0-18-8-7"),
        ]
        driver = FakeDriver([FakeTable([empty_row, FakeRow(populated_cells)])])

        self.assertEqual(
            extract_msan_port_from_equipment_table(driver),
            "MHOu-Fe-MourabitineERAC1--2C2:0-0-18-8",
        )

    def test_detects_no_available_fibre_port_business_error(self):
        message = Mock(text="pas de port disponible au niveau fibre optique {CLFI176526}")
        driver = FakeDriver([message])
        self.assertTrue(has_no_available_fibre_port(driver))

    def test_extracts_spl_from_old_constitution_table(self):
        table = Mock(text="Transport > OFOF-ZO-113.16 > PCO OFOF-ZO-2711")
        driver = FakeDriver([table])
        self.assertEqual(
            extract_spl_from_constitution(driver),
            "OFOF-ZO-113.16",
        )

    def test_msan_odf_fallback_rule(self):
        self.assertEqual(odf_with_msan("OFOF"), "OMSANFOF")
        self.assertEqual(odf_with_msan("OFAD33"), "OMSANFAD33")
        self.assertEqual(odf_with_msan("OMSANFOF"), "OMSANFOF")

    def test_invalid_odf_message_is_detected_from_ot_1(self):
        message = Mock(text="Nom du ODF invalide")
        driver = FakeDriver([message])
        self.assertTrue(has_invalid_odf_error(driver))

    def test_only_enabled_constitution_checkboxes_are_deletable(self):
        disabled = FakeCheckbox(enabled=False)
        enabled = FakeCheckbox(enabled=True)
        driver = FakeDriver([disabled, enabled])
        self.assertEqual(
            find_deletable_constitution_checkboxes(driver),
            [enabled],
        )

    @patch("wimtech_checker.WebDriverWait")
    @patch("wimtech_checker.click_element")
    @patch("wimtech_checker.submit_by_id")
    @patch("wimtech_checker.find_deletable_constitution_checkboxes")
    def test_deletion_uses_full_bsfb_validation_sequence(
        self,
        find_checkboxes,
        submit,
        click,
        wait_class,
    ):
        checkbox = FakeCheckbox(enabled=True, selected=False)
        motif = object()
        find_checkboxes.return_value = [checkbox]
        wait_class.side_effect = [
            ImmediateWait(),
            ImmediateWait(motif),
            ImmediateWait(),
            ImmediateWait(),
        ]

        deleted = delete_old_constitution(object(), 20)

        self.assertEqual(deleted, 1)
        self.assertEqual(
            submit.call_args_list,
            [
                call(ANY, "frm:dataTable82", 20),
                call(ANY, "frm:dataTable94", 20),
                call(ANY, "frm:v_but_va", 20),
                call(ANY, "frm:v_but_ano", 20),
            ],
        )
        self.assertEqual(click.call_args_list[0].args[1], checkbox)
        self.assertEqual(click.call_args_list[1].args[1], motif)

    @patch("wimtech_checker.WebDriverWait")
    @patch("wimtech_checker.submit_by_id")
    @patch("wimtech_checker.set_input")
    def test_pco_submit_retries_with_msan_after_invalid_odf(
        self,
        set_input,
        submit,
        wait_class,
    ):
        wait_class.side_effect = [
            ImmediateWait("INVALID_ODF"),
            ImmediateWait("EXISTS"),
        ]

        state, used_odf = submit_pco_location(
            object(),
            20,
            odf="OFOF",
            zr="OFOF-ZO",
            pco="OFOF-ZO-7122/2",
        )

        self.assertEqual(state, "EXISTS")
        self.assertEqual(used_odf, "OMSANFOF")
        odf_values = [
            item.args[2]
            for item in set_input.call_args_list
            if item.args[1] == "fr:inputOdf"
        ]
        self.assertEqual(odf_values, ["OFOF", "OMSANFOF"])
        self.assertEqual(submit.call_count, 2)


if __name__ == "__main__":
    unittest.main()
