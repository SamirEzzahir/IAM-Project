"""Exact Selenium workflow for the WimTech GPON PCO availability check."""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from selenium import webdriver
from selenium.common.exceptions import (
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from pco_logic import group_pco_candidates
from wimtech_parser import (
    base_result_confirms_existence,
    extract_available_fibre_ports,
    is_active_fo_cable,
    is_equipment_missing,
    normalize,
)


BASE_DIR = Path(__file__).resolve().parent
DIAGNOSTICS_DIR = BASE_DIR / "diagnostics"


def build_driver(headless: bool = False):
    options = webdriver.ChromeOptions()
    chrome_binary = os.getenv("CHROME_BINARY")
    if chrome_binary:
        options.binary_location = chrome_binary
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--allow-insecure-localhost")
    options.add_argument("--ignore-ssl-errors=yes")
    options.add_argument("--disable-notifications")
    options.add_argument("--start-maximized")
    force_headless = os.getenv("FORCE_CHROME_HEADLESS", "0").lower() in {
        "1", "true", "yes"
    }
    if headless or force_headless:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1280,1000")
    if os.getenv("CONTAINER", "0").lower() in {"1", "true", "yes"}:
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(options=options)


def wait_document(driver, timeout: int) -> None:
    WebDriverWait(driver, timeout).until(
        lambda current: current.execute_script("return document.readyState") == "complete"
    )


def wait_reload(driver, previous_document, timeout: int) -> None:
    """Wait for a JSF submit to replace the page, then wait for readyState."""

    try:
        WebDriverWait(driver, timeout).until(EC.staleness_of(previous_document))
    except TimeoutException:
        # Some WimTech/RichFaces transitions update the current document without
        # making Selenium's previous html element stale.
        pass
    wait_document(driver, timeout)


def set_input(driver, element_id: str, value: str, timeout: int) -> None:
    last_error = None
    for _ in range(4):
        try:
            field = WebDriverWait(driver, timeout).until(
                EC.visibility_of_element_located((By.ID, element_id))
            )
            field.clear()
            field.send_keys(value)
            return
        except StaleElementReferenceException as exc:
            last_error = exc
    if last_error:
        raise last_error


def click_element(driver, element, timeout: int) -> None:
    try:
        WebDriverWait(driver, timeout).until(lambda _: element.is_displayed() and element.is_enabled())
        element.click()
    except Exception:
        driver.execute_script("arguments[0].click();", element)


def click(driver, element_id: str, timeout: int) -> None:
    element = WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.ID, element_id))
    )
    click_element(driver, element, timeout)


def submit_by_id(driver, element_id: str, timeout: int) -> None:
    previous_document = driver.find_element(By.TAG_NAME, "html")
    click(driver, element_id, timeout)
    wait_reload(driver, previous_document, timeout)


def visible(driver, element_id: str) -> bool:
    try:
        return any(element.is_displayed() for element in driver.find_elements(By.ID, element_id))
    except Exception:
        return False


def body_text(driver) -> str:
    try:
        return driver.find_element(By.TAG_NAME, "body").text or ""
    except Exception:
        return ""


def has_login_error(driver) -> bool:
    return "PAS DE CIRCUIT ASSOCIE A CETTE LOGIN OU LOGIN ERRONE" in normalize(body_text(driver))


def has_invalid_odf_error(driver) -> bool:
    """Detect WimTech's exact invalid-ODF validation message."""

    expected = "NOM DU ODF INVALIDE"
    try:
        messages = driver.find_elements(By.ID, "ot_1")
        if any(expected in normalize(message.text) for message in messages):
            return True
    except Exception:
        pass
    return expected in normalize(body_text(driver))


def odf_with_msan(odf: str) -> str:
    """Convert OXXX to OMSANXXX for WimTech's alternate ODF naming."""

    value = str(odf or "").strip()
    if not value or not value.upper().startswith("O"):
        return value
    if value.upper().startswith("OMSAN"):
        return value
    return f"OMSAN{value[1:]}"


def select_search_mode(driver, timeout: int, mode: str) -> None:
    """Select a WimTech research radio and wait for its JSF submit."""

    previous_document = driver.find_element(By.TAG_NAME, "html")
    radio = WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable(
            (By.CSS_SELECTOR, f"input[name='frm:radionRechrche'][value='{mode}']")
        )
    )
    click_element(driver, radio, timeout)
    wait_reload(driver, previous_document, timeout)
    WebDriverWait(driver, timeout).until(
        EC.visibility_of_element_located((By.ID, "frm:in_2"))
    )


def select_login_mode(driver, timeout: int) -> None:
    """Select Login. Its onclick submits the form, so wait for the reload."""

    select_search_mode(driver, timeout, "Login")


def find_deletable_constitution_checkboxes(driver):
    """Return only enabled old-constitution checkboxes from the result table."""

    checkboxes = driver.find_elements(
        By.XPATH,
        "//*[@id='frm:constitutionList']//input[@type='checkbox' and not(@disabled)]",
    )
    return [checkbox for checkbox in checkboxes if checkbox.is_enabled()]


def delete_old_constitution(driver, timeout: int) -> int:
    """Delete the mutable downstream constitution before a real mutation.

    The first table row is normally disabled and represents the immutable
    upstream section. Every enabled row is selected so no stale downstream
    constitution remains before the new PCO is added.
    """

    checkboxes = find_deletable_constitution_checkboxes(driver)
    selected = 0
    for checkbox in checkboxes:
        if not checkbox.is_selected():
            click_element(driver, checkbox, timeout)
        selected += 1

    if not selected:
        return 0

    submit_by_id(driver, "frm:dataTable82", timeout)
    WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable((By.ID, "frm:dataTable94"))
    )
    submit_by_id(driver, "frm:dataTable94", timeout)

    motif = WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable(
            (By.CSS_SELECTOR, "input[name='frm:motif_menu'][value='BSFB']")
        )
    )
    click_element(driver, motif, timeout)
    submit_by_id(driver, "frm:v_but_va", timeout)
    WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable((By.ID, "frm:v_but_ano"))
    )
    submit_by_id(driver, "frm:v_but_ano", timeout)
    WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable((By.ID, "frm:dataTable81"))
    )
    return selected


def open_add_constitution_form(
    driver,
    timeout: int,
    *,
    delete_existing: bool = False,
) -> int:
    """Optionally delete the old constitution, then open the Ajouter form."""

    deleted_count = delete_old_constitution(driver, timeout) if delete_existing else 0
    submit_by_id(driver, "frm:dataTable81", timeout)
    for field_id in ("fr:inputOdf", "fr:inputZro", "fr:inputEquipAmont"):
        WebDriverWait(driver, timeout).until(
            EC.visibility_of_element_located((By.ID, field_id))
        )
    return deleted_count


def prepare_pco_form(
    driver,
    config: dict,
    login: str | None = None,
    *,
    delete_existing: bool = False,
) -> int:
    """Run the full Login research flow before every PCO.

    Availability checks use the configured MVP Login. Automatic assignment
    supplies the customer Login explicitly so the same navigation can be
    reused without changing the saved configuration.
    """

    timeout = int(config["timeout_seconds"])
    driver.get(config["wimtech_url"])
    wait_document(driver, timeout)

    select_login_mode(driver, timeout)
    searched_login = str(login or config["test_login"]).strip()
    set_input(driver, "frm:in_2", searched_login, timeout)
    submit_by_id(driver, "frm:bt_1", timeout)

    WebDriverWait(driver, timeout).until(
        lambda current: has_login_error(current)
        or bool(current.find_elements(By.ID, "frm:bt_2"))
    )
    if has_login_error(driver):
        raise ValueError(f"Login {searched_login} introuvable ou sans circuit associé.")

    submit_by_id(driver, "frm:bt_2", timeout)
    WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.ID, "frm:constitutionList"))
    )

    return open_add_constitution_form(
        driver,
        timeout,
        delete_existing=delete_existing,
    )


def equipment_missing(driver) -> bool:
    try:
        messages = driver.find_elements(By.ID, "ot_1")
        if any(is_equipment_missing(message.text) for message in messages):
            return True
    except Exception:
        pass
    return is_equipment_missing(body_text(driver))


def submit_pco_location(
    driver,
    timeout: int,
    *,
    odf: str,
    zr: str,
    pco: str,
) -> tuple[str, str]:
    """Submit a PCO, retrying once with OMSAN ODF naming when required."""

    def fill_and_submit(candidate_odf: str) -> None:
        set_input(driver, "fr:inputOdf", candidate_odf, timeout)
        set_input(driver, "fr:inputZro", zr, timeout)
        set_input(driver, "fr:inputEquipAmont", pco, timeout)
        submit_by_id(driver, "fr:b_et", timeout)

    def wait_state():
        return WebDriverWait(driver, timeout).until(
            lambda current: "INVALID_ODF" if has_invalid_odf_error(current)
            else "MISSING" if equipment_missing(current)
            else "EXISTS" if current.find_elements(By.ID, "frm:stp_1")
            else False
        )

    primary_odf = str(odf or "").strip()
    fill_and_submit(primary_odf)
    state = wait_state()
    if state != "INVALID_ODF":
        return state, primary_odf

    fallback_odf = odf_with_msan(primary_odf)
    if fallback_odf == primary_odf:
        raise RuntimeError(f"Nom du ODF invalide : {primary_odf}.")

    fill_and_submit(fallback_odf)
    state = wait_state()
    if state == "INVALID_ODF":
        raise RuntimeError(
            f"Nom du ODF invalide après les essais {primary_odf} et {fallback_odf}."
        )
    return state, fallback_odf


def find_active_cable_link(driver):
    try:
        anchors = driver.find_elements(By.XPATH, "//*[@id='frm:stp_1_body']//a")
    except Exception:
        return None

    for anchor in anchors:
        try:
            if is_active_fo_cable(anchor.text):
                return anchor
        except StaleElementReferenceException:
            continue
    return None


def open_active_cable(driver, timeout: int) -> str:
    cable = WebDriverWait(driver, timeout).until(
        lambda current: find_active_cable_link(current) or False
    )
    cable_label = " ".join((cable.text or "").split())
    previous_document = driver.find_element(By.TAG_NAME, "html")
    click_element(driver, cable, timeout)
    wait_reload(driver, previous_document, timeout)

    WebDriverWait(driver, timeout).until(
        lambda current: any(
            "(FIBRE-" in normalize(label.text)
            for label in current.find_elements(By.CSS_SELECTOR, "label.labelStyle")
        )
    )
    return cable_label


def collect_fibre_labels(driver) -> list[str]:
    labels: list[str] = []
    for element in driver.find_elements(By.CSS_SELECTOR, "label.labelStyle"):
        try:
            text = " ".join((element.text or "").split())
        except StaleElementReferenceException:
            continue
        if "(FIBRE-" in normalize(text):
            labels.append(text)
    return labels


def cancel_current_pco(driver, timeout: int) -> bool:
    """Click Annuler after a PCO test, as required by the WimTech flow."""

    if not visible(driver, "frm:bt_an"):
        return False
    try:
        submit_by_id(driver, "frm:bt_an", timeout)
        return True
    except Exception:
        return False


def save_diagnostic(driver, pco: str) -> str:
    DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", pco)
    path = DIAGNOSTICS_DIR / f"{safe_name}.html"
    path.write_text(driver.page_source, encoding="utf-8", errors="replace")
    return str(path.relative_to(BASE_DIR))


def check_one_pco(driver, config: dict, odf: str, zr: str, pco: str) -> dict:
    timeout = int(config["timeout_seconds"])
    prepare_pco_form(driver, config)

    state, used_odf = submit_pco_location(
        driver,
        timeout,
        odf=odf,
        zr=zr,
        pco=pco,
    )

    if state == "MISSING":
        cancel_current_pco(driver, min(timeout, 5))
        return {
            "status": "NOT_FOUND",
            "status_label": "Introuvable",
            "pco_exists": False,
            "free_ports": [],
            "free_count": 0,
            "odf_used": used_odf,
            "message": "Pas d’équipement installé au niveau de cette géolocalisation.",
        }

    try:
        cable_label = open_active_cable(driver, timeout)
    except TimeoutException:
        diagnostic = save_diagnostic(driver, pco)
        cancel_current_pco(driver, min(timeout, 5))
        return {
            "status": "UNKNOWN",
            "status_label": "À vérifier",
            "pco_exists": True,
            "free_ports": [],
            "free_count": 0,
            "odf_used": used_odf,
            "message": "PCO existant, mais aucun câble FO4/FO8-Active n’a été trouvé.",
            "diagnostic": diagnostic,
        }

    fibre_labels = collect_fibre_labels(driver)
    free_ports = extract_available_fibre_ports(fibre_labels)
    cancel_current_pco(driver, min(timeout, 8))

    if free_ports:
        return {
            "status": "AVAILABLE",
            "status_label": "Disponible",
            "pco_exists": True,
            "free_ports": free_ports,
            "free_count": len(free_ports),
            "odf_used": used_odf,
            "cable": cable_label,
            "message": f"Ports utilisables : {', '.join(free_ports)}.",
        }

    return {
        "status": "SATURATED",
        "status_label": "Saturé",
        "pco_exists": True,
        "free_ports": [],
        "free_count": 0,
        "odf_used": used_odf,
        "cable": cable_label,
        "message": "PCO existant, sans FIBRE-Libre ni En cours decon.",
    }


def check_all_pcos(
    *,
    config: dict,
    odf: str,
    zr: str,
    candidates: list[str],
    wait_if_paused: Callable[[], None],
    is_stopped: Callable[[], bool],
    on_log: Callable[[str, str], None],
    on_result: Callable[[int, dict], None],
) -> None:
    driver = None
    try:
        on_log("INFO", "Ouverture de Chrome et connexion à WimTech…")
        driver = build_driver(bool(config.get("headless", False)))

        def test_candidate(index: int, pco: str):
            wait_if_paused()
            if is_stopped():
                return None

            on_log(
                "INFO",
                f"Contrôle {index + 1}/{len(candidates)} : recherche Login puis PCO {pco}",
            )
            started_at = time.monotonic()
            try:
                result = check_one_pco(driver, config, odf, zr, pco)
            except TimeoutException:
                result = {
                    "status": "ERROR",
                    "status_label": "Erreur",
                    "free_ports": [],
                    "free_count": 0,
                    "message": "Délai WimTech dépassé.",
                }
            except Exception as exc:
                result = {
                    "status": "ERROR",
                    "status_label": "Erreur",
                    "free_ports": [],
                    "free_count": 0,
                    "message": str(exc),
                }

            result.update(
                {
                    "pco": pco,
                    "duration_seconds": round(time.monotonic() - started_at, 2),
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            on_result(index, result)
            on_log(
                "SUCCESS" if result["status"] == "AVAILABLE" else "INFO",
                f"{pco} : {result['status_label']} - {result['message']}",
            )
            return result

        for group_index, (base, split_1, split_2) in enumerate(
            group_pco_candidates(candidates)
        ):
            base_index = group_index * 3
            base_result = test_candidate(base_index, base)
            if base_result is None:
                on_log("WARNING", "Contrôle arrêté par l’utilisateur.")
                break

            if base_result_confirms_existence(base_result):
                for offset, split_pco in enumerate((split_1, split_2), start=1):
                    skipped = {
                        "pco": split_pco,
                        "status": "SKIPPED",
                        "status_label": "Ignoré",
                        "pco_exists": None,
                        "free_ports": [],
                        "free_count": 0,
                        "duration_seconds": 0,
                        "checked_at": datetime.now(timezone.utc).isoformat(),
                        "skipped_by": base,
                        "message": (
                            f"{base} existe en 8 FO : les formes /1 et /2 "
                            "ne sont pas testées."
                        ),
                    }
                    on_result(base_index + offset, skipped)
                    on_log("INFO", f"{split_pco} ignoré : {base} existe.")
                continue

            # The base is explicitly missing or its check was inconclusive.
            # Test both 4-FO split forms so a usable PCO is not missed.
            for offset, split_pco in enumerate((split_1, split_2), start=1):
                if test_candidate(base_index + offset, split_pco) is None:
                    on_log("WARNING", "Contrôle arrêté par l’utilisateur.")
                    return
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
        on_log("INFO", "Session Chrome fermée.")
