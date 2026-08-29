"""Bulk CMD/Login mutations using exact PCO and brin values from Excel."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable

from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from wimtech_checker import (
    body_text,
    build_driver,
    cancel_current_pco,
    click_element,
    has_login_error,
    has_no_available_fibre_port,
    open_add_constitution_form,
    open_active_cable,
    save_diagnostic,
    select_search_mode,
    set_input,
    submit_pco_location,
    submit_by_id,
    wait_document,
    wait_for_action_or_port_error,
)
from wimtech_parser import normalize, parse_fibre_label


SPL_PATTERN = r"\b[A-Z0-9]+-ZO-[A-Z0-9]+(?:\.[A-Z0-9]+)+\b"


class NoPortAvailableError(Exception):
    pass


def extract_spl_from_constitution(driver) -> str | None:
    """Read the SPL from the existing frm:constitutionList before deletion."""

    import re

    tables = driver.find_elements(By.ID, "frm:constitutionList")
    if not tables:
        return None
    match = re.search(SPL_PATTERN, normalize(tables[0].text))
    return match.group(0) if match else None


def has_command_error(driver) -> bool:
    expected = "PAS DE CIRCUIT ASSOCIE A CETTE COMMANDE OU LA COMMANDE EST DEJA MISE EN SERVICE"
    try:
        messages = driver.find_elements(By.ID, "frm:ot_4")
        if any(expected in normalize(message.text) for message in messages):
            return True
    except Exception:
        pass
    return expected in normalize(body_text(driver))


def open_pco_form_for_bulk(
    driver, config: dict, command: str, login: str
) -> tuple[str, str | None]:
    """Try CMD/NNETO first, then Login only when the CMD circuit is absent."""

    timeout = int(config["timeout_seconds"])

    def research(mode: str, value: str) -> bool:
        driver.get(config["wimtech_url"])
        wait_document(driver, timeout)
        select_search_mode(driver, timeout, mode)
        set_input(driver, "frm:in_2", value, timeout)
        submit_by_id(driver, "frm:bt_1", timeout)
        WebDriverWait(driver, timeout).until(
            lambda current: has_command_error(current)
            or has_login_error(current)
            or bool(current.find_elements(By.ID, "frm:bt_2"))
        )
        if mode == "NNETO" and has_command_error(driver):
            return False
        if mode == "Login" and has_login_error(driver):
            return False
        if not driver.find_elements(By.ID, "frm:bt_2"):
            raise ValueError(f"Résultat de recherche WimTech non reconnu en mode {mode}.")

        submit_by_id(driver, "frm:bt_2", timeout)
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.ID, "frm:constitutionList"))
        )
        spl = extract_spl_from_constitution(driver)
        open_add_constitution_form(driver, timeout, delete_existing=True)
        return spl

    if command:
        spl = research("NNETO", command)
        if spl is not False:
            return "CMD", spl
    if login:
        spl = research("Login", login)
        if spl is not False:
            return "Login", spl
    if command and not login:
        raise ValueError(
            f"Commande {command} sans circuit associé et aucun Login de secours."
        )
    raise ValueError(
        f"Aucun circuit trouvé par CMD {command or '—'} ni par Login {login or '—'}."
    )


def find_target_fibre_action(driver, target_port: str):
    """Find the exact brin and its Muter-vers link, regardless of fibre state."""

    normalized_target = str(int(str(target_port)))
    for label in driver.find_elements(By.CSS_SELECTOR, "label.labelStyle"):
        try:
            label_text = " ".join((label.text or "").split())
            details = parse_fibre_label(label_text)
            if not details or details["port"] != normalized_target:
                continue

            label_id = label.get_attribute("id") or ""
            links = []
            if label_id.startswith("frm:l_"):
                link_id = label_id.replace("frm:l_", "frm:li_", 1)
                links = driver.find_elements(By.ID, link_id)
            if not links:
                links = label.find_elements(
                    By.XPATH,
                    "../a[@title='Muter vers'] | ../descendant::a[@title='Muter vers']",
                )
            return details, label_text, (links[0] if links else None)
        except StaleElementReferenceException:
            continue
    return None


def mutate_bulk_row(driver, config: dict, row: dict) -> dict:
    timeout = int(config["timeout_seconds"])
    search_mode, spl = open_pco_form_for_bulk(
        driver,
        config,
        row.get("command", ""),
        row.get("login", ""),
    )

    state, used_odf = submit_pco_location(
        driver,
        timeout,
        odf=row["odf"],
        zr=row["zr"],
        pco=row["pco"],
    )
    if state == "MISSING":
        cancel_current_pco(driver, min(timeout, 5))
        return {
            "status": "NOT_FOUND",
            "status_label": "PCO inexistant",
            "search_mode": search_mode,
            "previous_login": None,
            "spl": spl,
            "odf_used": used_odf,
            "message": f"PCO introuvable : {row['pco']}.",
        }

    try:
        cable_label = open_active_cable(driver, timeout)
    except TimeoutException:
        diagnostic = save_diagnostic(driver, f"bulk_{row['excel_row']}_{row['pco']}")
        cancel_current_pco(driver, min(timeout, 5))
        return {
            "status": "UNKNOWN",
            "status_label": "À vérifier",
            "search_mode": search_mode,
            "previous_login": None,
            "spl": spl,
            "odf_used": used_odf,
            "diagnostic": diagnostic,
            "message": "Aucun câble FO4/FO8-Active trouvé.",
        }

    target_brin = row.get("target_brin") or row["brin"]
    target = find_target_fibre_action(driver, target_brin)
    if not target:
        cancel_current_pco(driver, min(timeout, 8))
        return {
            "status": "BRIN_NOT_FOUND",
            "status_label": "Brin introuvable",
            "search_mode": search_mode,
            "previous_login": None,
            "spl": spl,
            "odf_used": used_odf,
            "cable": cable_label,
            "message": f"Le brin {target_brin} n’existe pas dans ce PCO.",
        }

    details, fibre_label, plus_link = target
    previous_login = details.get("current_login")
    if plus_link is None:
        cancel_current_pco(driver, min(timeout, 8))
        return {
            "status": "NO_MUTATION_ACTION",
            "status_label": "Action absente",
            "search_mode": search_mode,
            "previous_login": previous_login,
            "spl": spl,
            "odf_used": used_odf,
            "cable": cable_label,
            "fibre_label": fibre_label,
            "message": f"Le brin {target_brin} n’a pas de lien Muter vers (+).",
        }

    # Save the Login currently displayed on the target FIBRE-Active row before
    # clicking Muter vers. Libre rows naturally produce an empty previous Login.
    try:
        click_element(driver, plus_link, timeout)
        if not wait_for_action_or_port_error(driver, timeout, "frm:dataTable94"):
            raise NoPortAvailableError
        submit_by_id(driver, "frm:dataTable94", timeout)
        if not wait_for_action_or_port_error(driver, timeout, "frm:bt_va"):
            raise NoPortAvailableError
        submit_by_id(driver, "frm:bt_va", timeout)
        if not wait_for_action_or_port_error(driver, timeout, "frm:v_but_ano"):
            raise NoPortAvailableError
    except NoPortAvailableError:
        return {
            "status": "PORT_UNAVAILABLE",
            "status_label": "Port indisponible",
            "search_mode": search_mode,
            "previous_login": previous_login,
            "spl": spl,
            "odf_used": used_odf,
            "cable": cable_label,
            "fibre_label": fibre_label,
            "message": (
                "WimTech indique : pas de port disponible au niveau fibre optique. "
                "Passage à la ligne suivante."
            ),
        }
    except Exception:
        if has_no_available_fibre_port(driver):
            return {
                "status": "PORT_UNAVAILABLE", "status_label": "Port indisponible",
                "search_mode": search_mode, "previous_login": previous_login,
                "spl": spl, "odf_used": used_odf, "cable": cable_label,
                "fibre_label": fibre_label,
                "message": "WimTech indique : pas de port disponible au niveau fibre optique. Passage à la ligne suivante.",
            }
        diagnostic = save_diagnostic(
            driver,
            f"bulk_{row['excel_row']}_{row['pco']}_mutation_inconnue",
        )
        return {
            "status": "MUTATION_UNKNOWN",
            "status_label": "À confirmer",
            "search_mode": search_mode,
            "previous_login": previous_login,
            "spl": spl,
            "odf_used": used_odf,
            "cable": cable_label,
            "fibre_label": fibre_label,
            "halt": True,
            "diagnostic": diagnostic,
            "message": (
                "Mutation commencée, mais confirmation finale incertaine. "
                "Le traitement Bulk est arrêté pour éviter une double mutation."
            ),
        }

    close_warning = ""
    try:
        submit_by_id(driver, "frm:v_but_ano", min(timeout, 10))
    except Exception:
        close_warning = " Confirmation acquise, mais la fenêtre n’a pas pu être fermée."

    requested = row.get("login") or row.get("command")
    return {
        "status": "MUTATED",
        "status_label": "Muté",
        "search_mode": search_mode,
        "previous_login": previous_login,
        "spl": spl,
        "odf_used": used_odf,
        "cable": cable_label,
        "fibre_label": fibre_label,
        "message": (
            f"{requested} muté vers {row['pco']} brin {target_brin}."
            f"{close_warning}"
        ),
    }


def mutate_bulk_rows(
    *,
    config: dict,
    rows: list[dict],
    is_stopped: Callable[[], bool],
    on_log: Callable[[str, str], None],
    on_result: Callable[[int, dict], None],
) -> None:
    driver = None
    try:
        on_log("INFO", f"Ouverture de Chrome pour {len(rows)} mutation(s) Bulk…")
        driver = build_driver(bool(config.get("headless", False)))
        for index, row in enumerate(rows):
            if is_stopped():
                on_log("WARNING", "Bulk Mutation arrêté par l’utilisateur.")
                break

            started_at = time.monotonic()
            on_log(
                "INFO",
                f"Ligne {row['excel_row']} : CMD {row['command'] or '—'} / "
                f"Login {row['login'] or '—'} / {row['pco'] or '—'} brin {row['brin'] or '—'}",
            )
            if row.get("validation_error"):
                result = {
                    "status": "INVALID",
                    "status_label": "Ligne invalide",
                    "search_mode": None,
                    "previous_login": None,
                    "message": row["validation_error"],
                }
            else:
                try:
                    result = mutate_bulk_row(driver, config, row)
                except ValueError as exc:
                    result = {
                        "status": "SEARCH_FAILED",
                        "status_label": "Circuit introuvable",
                        "search_mode": None,
                        "previous_login": None,
                        "message": str(exc),
                    }
                except TimeoutException:
                    result = {
                        "status": "ERROR",
                        "status_label": "Délai dépassé",
                        "search_mode": None,
                        "previous_login": None,
                        "message": "Délai WimTech dépassé avant la mutation.",
                    }
                except Exception as exc:
                    result = {
                        "status": "ERROR",
                        "status_label": "Erreur",
                        "search_mode": None,
                        "previous_login": None,
                        "message": str(exc),
                    }

            result.update({
                "excel_row": row["excel_row"],
                "command": row["command"],
                "login": row["login"],
                "pco": row["pco"],
                "brin": row["brin"],
                "target_brin": row.get("target_brin"),
                "duration_seconds": round(time.monotonic() - started_at, 2),
                "checked_at": datetime.now(timezone.utc).isoformat(),
            })
            on_result(index, result)
            level = "SUCCESS" if result["status"] == "MUTATED" else (
                "ERROR" if result["status"] in {"ERROR", "MUTATION_UNKNOWN"} else "INFO"
            )
            on_log(level, f"Ligne {row['excel_row']} : {result['status_label']} - {result['message']}")
            if result.get("halt"):
                break
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
        on_log("INFO", "Session Chrome Bulk Mutation fermée.")
