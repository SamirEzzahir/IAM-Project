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

from pco_logic import alternate_prefixed_pco, group_pco_candidates, prefixed_111_pco
from wimtech_parser import (
    base_result_confirms_existence,
    extract_available_fibre_ports,
    is_active_fo_cable,
    is_equipment_missing,
    normalize,
    build_msan_port_key,
)


BASE_DIR = Path(__file__).resolve().parent
DIAGNOSTICS_DIR = BASE_DIR / "diagnostics"
MAX_DIAGNOSTICS = 20


def action_delay_from_config(config: dict) -> float:
    """Return the configured per-action pause only while debug mode is enabled."""

    return float(config.get("action_delay_seconds", 0) or 0) if config.get("debug_mode") else 0.0


def selenium_action_delay(driver) -> None:
    delay = float(getattr(driver, "_fb_action_delay_seconds", 0) or 0)
    if delay > 0:
        time.sleep(delay)


def navigate(driver, url: str) -> None:
    selenium_action_delay(driver)
    driver.get(url)


def build_driver(headless: bool = False, *, ignore_certificate_errors: bool = False, action_delay_seconds: float = 0):
    options = webdriver.ChromeOptions()
    chrome_binary = os.getenv("CHROME_BINARY")
    if chrome_binary:
        options.binary_location = chrome_binary
    if ignore_certificate_errors or os.getenv("ALLOW_INSECURE_CERTIFICATES", "0").lower() in {"1", "true", "yes"}:
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
    driver = webdriver.Chrome(options=options)
    driver._fb_action_delay_seconds = max(0.0, float(action_delay_seconds or 0))
    return driver


def close_driver(driver) -> None:
    """Close a workflow-owned Chrome session without masking its result."""

    if driver is None:
        return
    try:
        driver.quit()
    except Exception:
        pass


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
    expected_value = str(value or "")
    for _ in range(4):
        try:
            field = WebDriverWait(driver, timeout).until(
                EC.visibility_of_element_located((By.ID, element_id))
            )
            selenium_action_delay(driver)
            field.clear()
            selenium_action_delay(driver)
            field.send_keys(expected_value)

            # Some internal pages install global keyboard shortcuts. A typed
            # "t" can consequently be consumed instead of reaching the
            # Login field. Never submit a silently altered identifier.
            actual_value = field.get_attribute("value") or ""
            if actual_value != expected_value:
                driver.execute_script(
                    """
                    const field = arguments[0];
                    const value = arguments[1];
                    const prototype = field instanceof HTMLTextAreaElement
                        ? HTMLTextAreaElement.prototype
                        : HTMLInputElement.prototype;
                    const setter = Object.getOwnPropertyDescriptor(
                        prototype, 'value'
                    )?.set;
                    if (setter) setter.call(field, value);
                    else field.value = value;
                    field.dispatchEvent(new Event('input', { bubbles: true }));
                    field.dispatchEvent(new Event('change', { bubbles: true }));
                    """,
                    field,
                    expected_value,
                )
                actual_value = field.get_attribute("value") or ""
            if actual_value == expected_value:
                return
            last_error = RuntimeError(
                f"La page a modifié la valeur du champ {element_id} : "
                f"{actual_value!r} au lieu de {expected_value!r}."
            )
        except StaleElementReferenceException as exc:
            last_error = exc
    if last_error:
        raise last_error


def click_element(driver, element, timeout: int) -> None:
    selenium_action_delay(driver)
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


def has_no_available_fibre_port(driver) -> bool:
    """Detect the business error returned after mutation validation."""

    expected = "PAS DE PORT DISPONIBLE AU NIVEAU FIBRE OPTIQUE"
    try:
        messages = driver.find_elements(By.ID, "frm:ot_1")
        if any(expected in normalize(message.text) for message in messages):
            return True
    except Exception:
        pass
    return expected in normalize(body_text(driver))


def wait_for_action_or_port_error(driver, timeout: int, action_id: str) -> bool:
    """Return False for the known no-port error, True when action is ready."""

    state = WebDriverWait(driver, timeout).until(
        lambda current: "NO_PORT" if has_no_available_fibre_port(current)
        else "READY" if current.find_elements(By.ID, action_id)
        else False
    )
    return state == "READY"


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


def extract_current_constitution(driver) -> dict[str, str]:
    """Read the existing downstream SPL/PCO/brin from constitutionList."""

    tables = driver.find_elements(By.ID, "frm:constitutionList")
    if not tables:
        return {"spl": "", "pco": "", "brin": ""}
    tbody = tables[0].find_element(By.TAG_NAME, "tbody")
    aval_index = 0
    spl = pco = brin = ""
    for row in tbody.find_elements(By.TAG_NAME, "tr"):
        cells = row.find_elements(By.TAG_NAME, "td")
        if len(cells) < 10:
            continue
        aval_index += 1
        geo_aval = (cells[9].text or "").strip()
        if aval_index == 1:
            spl = geo_aval
        elif aval_index == 2:
            pco = geo_aval
            spl = (cells[1].text or "").strip() or spl
            brin = (cells[8].text or "").strip()
            break
    return {"spl": spl, "pco": pco, "brin": brin}


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
    navigate(driver, config["wimtech_url"])
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


def extract_msan_port_from_equipment_table(driver) -> str:
    """Extract Nom Usuel + Ne from frm:NumeroEquipementGPON."""

    tables = driver.find_elements(By.ID, "frm:NumeroEquipementGPON")
    if not tables:
        raise ValueError("Table NumeroEquipementGPON introuvable pour ce Login.")

    # RichFaces can expose the table (and sometimes an empty transition row)
    # before Selenium's rendered ``.text`` value is ready.  ``textContent``
    # already contains the server value in that situation, so prefer it as a
    # fallback and keep looking until a genuinely usable row is found.
    invalid_values: list[tuple[str, str]] = []
    for table in tables:
        for row in table.find_elements(By.CSS_SELECTOR, "tbody tr"):
            cells = row.find_elements(By.CSS_SELECTOR, "td")
            if len(cells) < 6:
                continue

            def cell_text(cell) -> str:
                rendered = " ".join(str(cell.text or "").split()).strip()
                if rendered:
                    return rendered
                content = cell.get_attribute("textContent") or ""
                return " ".join(str(content).split()).strip()

            nom_usuel = cell_text(cells[1])
            ne = cell_text(cells[5])
            if not nom_usuel and not ne:
                continue
            try:
                return build_msan_port_key(nom_usuel, ne)
            except ValueError:
                invalid_values.append((nom_usuel, ne))

    if invalid_values:
        nom_usuel, ne = invalid_values[-1]
        raise ValueError(
            "Valeurs NumeroEquipementGPON inexploitables "
            f"(Nom Usuel={nom_usuel or '∅'}, Ne={ne or '∅'})."
        )
    raise ValueError(
        "La table NumeroEquipementGPON est présente, mais ses cellules "
        "Nom Usuel et Ne ne sont pas encore lisibles."
    )


def wait_for_msan_port_from_equipment_table(driver, timeout: int) -> str:
    """Wait for RichFaces to finish populating the equipment table."""

    def extract_when_ready(current):
        try:
            return extract_msan_port_from_equipment_table(current)
        except (ValueError, StaleElementReferenceException):
            return False

    try:
        return WebDriverWait(driver, timeout).until(extract_when_ready)
    except TimeoutException as exc:
        # Re-run once to expose the most useful table/value error instead of a
        # generic Selenium timeout.
        try:
            return extract_msan_port_from_equipment_table(driver)
        except ValueError as table_error:
            raise table_error from exc


def open_login_constitution_page(driver, config: dict, login: str) -> None:
    """Open a Login's constitution page without requiring its MSAN table."""
    timeout = int(config["timeout_seconds"])
    navigate(driver, config["wimtech_url"])
    wait_document(driver, timeout)
    select_login_mode(driver, timeout)
    set_input(driver, "frm:in_2", login, timeout)
    submit_by_id(driver, "frm:bt_1", timeout)
    WebDriverWait(driver, timeout).until(
        lambda current: has_login_error(current)
        or bool(current.find_elements(By.ID, "frm:bt_2"))
    )
    if has_login_error(driver):
        raise ValueError(f"Login {login} introuvable ou sans circuit associé.")
    submit_by_id(driver, "frm:bt_2", timeout)
    WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.ID, "frm:constitutionList"))
    )


def open_login_constitution(driver, config: dict, login: str) -> str:
    """Open a Login's constitution and return its normalized MSAN port key."""

    timeout = int(config["timeout_seconds"])
    open_login_constitution_page(driver, config, login)
    return wait_for_msan_port_from_equipment_table(driver, timeout)


def lookup_login_msan_port(config: dict, login: str) -> str:
    """Search a Login and return its normalized MSAN port mapping key."""

    timeout = int(config["timeout_seconds"])
    driver = build_driver(bool(config.get("headless", False)), action_delay_seconds=action_delay_from_config(config))
    try:
        navigate(driver, config["wimtech_url"])
        wait_document(driver, timeout)
        select_login_mode(driver, timeout)
        set_input(driver, "frm:in_2", login, timeout)
        submit_by_id(driver, "frm:bt_1", timeout)
        WebDriverWait(driver, timeout).until(
            lambda current: has_login_error(current)
            or bool(current.find_elements(By.ID, "frm:bt_2"))
        )
        if has_login_error(driver):
            raise ValueError(f"Login {login} introuvable ou sans circuit associé.")
        submit_by_id(driver, "frm:bt_2", timeout)
        return wait_for_msan_port_from_equipment_table(driver, timeout)
    finally:
        close_driver(driver)


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

    def fill_and_submit(candidate_odf: str, candidate_pco: str) -> None:
        set_input(driver, "fr:inputOdf", candidate_odf, timeout)
        set_input(driver, "fr:inputZro", zr, timeout)
        set_input(driver, "fr:inputEquipAmont", candidate_pco, timeout)
        # The study action may update the current JSF document without making
        # the old <html> element stale. Waiting for a full reload here can look
        # like a freeze for the entire configured timeout. Click, then let
        # wait_state() observe the actual WimTech business result directly.
        click(driver, "fr:b_et", timeout)

    def wait_state():
        return WebDriverWait(driver, timeout).until(
            lambda current: "INVALID_ODF" if has_invalid_odf_error(current)
            else "MISSING" if equipment_missing(current)
            else "EXISTS" if current.find_elements(By.ID, "frm:stp_1")
            else False
        )

    primary_odf = str(odf or "").strip()
    candidate_pcos = [pco]
    prefixed_pco = prefixed_111_pco(pco)
    if prefixed_pco:
        candidate_pcos.append(prefixed_pco)

    used_odf = primary_odf
    for candidate_pco in candidate_pcos:
        fill_and_submit(used_odf, candidate_pco)
        state = wait_state()
        if state == "INVALID_ODF":
            fallback_odf = odf_with_msan(used_odf)
            if fallback_odf == used_odf:
                raise RuntimeError(f"Nom du ODF invalide : {used_odf}.")
            fill_and_submit(fallback_odf, candidate_pco)
            state = wait_state()
            if state == "INVALID_ODF":
                raise RuntimeError(
                    f"Nom du ODF invalide après les essais {used_odf} et {fallback_odf}."
                )
            used_odf = fallback_odf
        if state != "MISSING" or candidate_pco == candidate_pcos[-1]:
            return state, used_odf

    return "MISSING", used_odf


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
    files = sorted(
        DIAGNOSTICS_DIR.glob("*.html"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for stale in files[MAX_DIAGNOSTICS:]:
        try:
            stale.unlink()
        except OSError:
            pass
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
        driver = build_driver(bool(config.get("headless", False)), action_delay_seconds=action_delay_from_config(config))

        def test_candidate(index: int, pco: str):
            wait_if_paused()
            if is_stopped():
                return None

            on_log(
                "INFO",
                f"Contrôle {index + 1}/{len(candidates)} : recherche Login puis PCO {pco}",
            )
            started_at = time.monotonic()
            used_pco = pco
            try:
                result = check_one_pco(driver, config, odf, zr, pco)
                fallback = alternate_prefixed_pco(pco)
                if result.get("status") == "NOT_FOUND" and fallback:
                    on_log("INFO", f"{pco} introuvable : nouvel essai avec {fallback}")
                    used_pco = fallback
                    result = check_one_pco(driver, config, odf, zr, fallback)
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
                    "pco": used_pco,
                    "duration_seconds": round(time.monotonic() - started_at, 2),
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            on_result(index, result)
            on_log(
                "SUCCESS" if result["status"] == "AVAILABLE" else "INFO",
                f"{used_pco} : {result['status_label']} - {result['message']}",
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
                    displayed_split = (
                        alternate_prefixed_pco(split_pco)
                        if base_result.get("pco") != base
                        else split_pco
                    ) or split_pco
                    skipped = {
                        "pco": displayed_split,
                        "status": "SKIPPED",
                        "status_label": "Ignoré",
                        "pco_exists": None,
                        "free_ports": [],
                        "free_count": 0,
                        "duration_seconds": 0,
                        "checked_at": datetime.now(timezone.utc).isoformat(),
                        "skipped_by": base_result["pco"],
                        "message": (
                            f"{base_result['pco']} existe en 8 FO : les formes /1 et /2 "
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
        close_driver(driver)
        on_log("INFO", "Session Chrome fermée.")
