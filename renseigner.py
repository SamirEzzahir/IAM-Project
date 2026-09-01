"""WIAM Login and WimTech current-constitution collection."""

from __future__ import annotations

import time
from typing import Callable

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from wimtech_checker import (
    build_driver, close_driver, has_login_error, select_search_mode, set_input,
    submit_by_id, wait_document, wait_for_msan_port_from_equipment_table,
)
from wimtech_bulk_mutator import ensure_in_progress_circuit_selected, has_command_error


def _wiam_login(driver, config: dict, timeout: int) -> None:
    if driver.find_elements(By.ID, "login"):
        set_input(driver, "login", config["wiam_username"], timeout)
        set_input(driver, "password", config["wiam_password"], timeout)
        driver.execute_script("doLog();")
        WebDriverWait(driver, timeout).until(lambda current: not current.find_elements(By.ID, "login"))


def collect_wiam_login(driver, config: dict, command: str) -> str:
    timeout = int(config["timeout_seconds"])
    if not all(config.get(key) for key in ("wiam_url", "wiam_username", "wiam_password")):
        raise ValueError("Configurez l’URL, le Login et le mot de passe WIAM.")
    driver.get(config["wiam_url"])
    _wiam_login(driver, config, timeout)
    # WIAM often opens on Accueil even when the Commandes URL was requested.
    # Follow the same Commandes top-bar link that the user clicks manually.
    if not driver.find_elements(By.NAME, "num_commande"):
        command_link = next(
            (
                link for link in driver.find_elements(By.TAG_NAME, "a")
                if "commande_recherche_critere.jsp" in (link.get_attribute("href") or "").lower()
            ),
            None,
        )
        if not command_link:
            raise ValueError("Lien WIAM Commandes introuvable depuis la page Accueil.")
        driver.execute_script("arguments[0].click();", command_link)
    field = WebDriverWait(driver, timeout).until(lambda current: current.find_element(By.NAME, "num_commande"))
    radios = driver.find_elements(By.CSS_SELECTOR, "input[type='radio'][value='1']")
    if radios and not radios[0].is_selected():
        radios[0].click()
    field.clear(); field.send_keys(command)
    buttons = driver.find_elements(By.ID, "xx")
    if buttons:
        buttons[0].click()
    else:
        link = next((item for item in driver.find_elements(By.TAG_NAME, "a") if "chercher" in (item.text or "").lower()), None)
        if not link:
            raise ValueError("Bouton Chercher WIAM introuvable.")
        link.click()
    cells = WebDriverWait(driver, timeout).until(lambda current: current.find_elements(By.CSS_SELECTOR, "td.datalistfield") or False)
    if len(cells) < 2 or not (cells[1].text or "").strip():
        raise ValueError(f"Aucun Login WIAM trouvé pour {command}.")
    return cells[1].text.strip()


def collect_constitution(
    driver, config: dict, login: str, command: str | None = None
) -> dict[str, str]:
    timeout = int(config["timeout_seconds"])

    def search(mode: str, value: str) -> bool:
        driver.get(config["wimtech_url"])
        wait_document(driver, timeout)
        select_search_mode(driver, timeout, mode)
        set_input(driver, "frm:in_2", value, timeout)
        submit_by_id(driver, "frm:bt_1", timeout)
        WebDriverWait(driver, timeout).until(
            lambda current: has_login_error(current)
            or has_command_error(current)
            or current.find_elements(By.ID, "frm:bt_2")
        )
        if mode == "Login" and has_login_error(driver):
            return False
        if mode == "NNETO" and has_command_error(driver):
            return False
        ensure_in_progress_circuit_selected(driver, timeout)
        return bool(driver.find_elements(By.ID, "frm:bt_2"))

    search_mode = "Login"
    if not search("Login", login):
        if not command or not search("NNETO", command):
            raise ValueError(
                f"Aucun circuit trouvé par Login {login}"
                + (f" ni par CMD {command}." if command else ".")
            )
        search_mode = "CMD"
    submit_by_id(driver, "frm:bt_2", timeout)
    table = WebDriverWait(driver, timeout).until(
        lambda current: current.find_element(By.ID, "frm:constitutionList")
    )
    # Keep the proven get_const.py extraction exactly: find descendant TDs,
    # including the nested Position table.  This makes indexes 9, 1 and 8
    # correspond to AVAL geolocation, AMONT geolocation and brin respectively.
    tbody = table.find_element(By.TAG_NAME, "tbody")
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
            spl = (cells[1].text or "").strip()
            brin = (cells[8].text or "").strip()
            break
    try:
        msan_port = wait_for_msan_port_from_equipment_table(driver, timeout)
    except (TimeoutException, ValueError):
        msan_port = ""
    return {
        "constitution_spl": spl,
        "constitution_pco": pco,
        "constitution_brin": brin,
        "msan_port": msan_port,
        "constitution_search_mode": search_mode,
    }


def run_renseigner(*, config: dict, rows: list[dict], degroupage: dict, stopped: Callable[[], bool], on_result: Callable[[int, dict], None], on_log: Callable[[str, str], None]) -> None:
    # WIAM may use an internal certificate that is not trusted by Chrome.
    driver = build_driver(
        bool(config.get("headless", False)),
        ignore_certificate_errors=True,
    )
    try:
        for index, row in enumerate(rows):
            if stopped(): break
            started = time.monotonic()
            result = {**row, "login": row.get("login", ""), "source": "", "constitution_search_mode": "", "constitution_spl": "", "constitution_pco": "", "constitution_brin": "", "msan_port": ""}
            try:
                if row["mode"] in {"CMD", "BOTH"}:
                    command = row["input"].upper()
                    mapped = degroupage.get(command) if command.startswith("DFOI") else None
                    if mapped:
                        result.update(login=mapped.get("login", ""), source="Degroupage Excel")
                        if not result["login"]: raise ValueError(f"Login vide pour {command} dans Degroupage.")
                    else:
                        result.update(login=collect_wiam_login(driver, config, command), source="WIAM")
                if row["mode"] in {"LOGIN", "BOTH"}:
                    login = row["input"] if row["mode"] == "LOGIN" else result["login"]
                    result["login"] = login
                    command = row["input"] if row["mode"] == "BOTH" else None
                    result.update(collect_constitution(driver, config, login, command))
                result.update(status="COMPLETED", status_label="Terminé", message="Collecte terminée.")
            except Exception as exc:
                result.update(status="ERROR", status_label="Erreur", message=str(exc))
            result["duration_seconds"] = round(time.monotonic() - started, 2)
            on_result(index, result)
            on_log("SUCCESS" if result["status"] == "COMPLETED" else "ERROR", f"Ligne {row['excel_row']} : {result['message']}")
    finally:
        close_driver(driver)
