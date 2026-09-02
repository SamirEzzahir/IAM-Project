"""Automatic WimTech assignment of a Login to the first usable PCO port."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable

from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from pco_logic import alternate_prefixed_pco, group_pco_candidates
from wimtech_checker import (
    build_driver,
    action_delay_from_config,
    close_driver,
    cancel_current_pco,
    click_element,
    has_no_available_fibre_port,
    open_active_cable,
    open_add_constitution_form,
    prepare_pco_form,
    save_diagnostic,
    submit_pco_location,
    submit_by_id,
    wait_for_action_or_port_error,
)
from wimtech_parser import base_result_confirms_existence, usable_fibre_port


class NoPortAvailableError(Exception):
    pass


def find_first_usable_port_action(driver):
    """Return ``(port, label, plus_link)`` for the first usable fibre row."""

    for label in driver.find_elements(By.CSS_SELECTOR, "label.labelStyle"):
        try:
            label_text = " ".join((label.text or "").split())
            port = usable_fibre_port(label_text)
            if not port:
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
            if links:
                return port, label_text, links[0]
        except StaleElementReferenceException:
            continue
    return None


def assign_one_pco(
    driver,
    config: dict,
    *,
    login: str,
    odf: str,
    zr: str,
    pco: str,
    form_ready: bool = False,
) -> dict:
    """Inspect one PCO and complete the mutation on its first usable port."""

    timeout = int(config["timeout_seconds"])
    deleted_count = 0 if form_ready else prepare_pco_form(
        driver, config, login=login, delete_existing=True,
    )
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
            "status_label": "Inexistant",
            "pco_exists": False,
            "selected_port": None,
            "deleted_constitutions": deleted_count,
            "odf_used": used_odf,
            "message": "Pas d’équipement installé à cette géolocalisation.",
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
            "selected_port": None,
            "deleted_constitutions": deleted_count,
            "odf_used": used_odf,
            "message": "PCO existant, mais aucun câble FO4/FO8-Active n’a été trouvé.",
            "diagnostic": diagnostic,
        }

    action = find_first_usable_port_action(driver)
    if not action:
        cancel_current_pco(driver, min(timeout, 8))
        return {
            "status": "SATURATED",
            "status_label": "Saturé",
            "pco_exists": True,
            "selected_port": None,
            "deleted_constitutions": deleted_count,
            "odf_used": used_odf,
            "cable": cable_label,
            "message": "Aucun port FIBRE-Libre ou En cours decon.",
        }

    port, fibre_label, plus_link = action
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
        # The business error leaves Chrome inside the current mutation flow.
        # Return to the constitution page so the same session can test the
        # next candidate PCO.
        cancel_current_pco(driver, min(timeout, 8))
        return {
            "status": "SATURATED",
            "status_label": "Port indisponible",
            "pco_exists": True,
            "selected_port": port,
            "deleted_constitutions": deleted_count,
            "odf_used": used_odf,
            "cable": cable_label,
            "message": "WimTech indique : pas de port disponible au niveau fibre optique.",
        }
    except Exception:
        if has_no_available_fibre_port(driver):
            cancel_current_pco(driver, min(timeout, 8))
            return {
                "status": "SATURATED", "status_label": "Port indisponible",
                "pco_exists": True, "selected_port": port,
                "deleted_constitutions": deleted_count, "odf_used": used_odf,
                "cable": cable_label,
                "message": "WimTech indique : pas de port disponible au niveau fibre optique.",
            }
        diagnostic = save_diagnostic(driver, f"{pco}_mutation_inconnue")
        return {
            "status": "MUTATION_UNKNOWN",
            "status_label": "À confirmer",
            "pco_exists": True,
            "selected_port": port,
            "deleted_constitutions": deleted_count,
            "odf_used": used_odf,
            "cable": cable_label,
            "halt": True,
            "diagnostic": diagnostic,
            "message": (
                "La mutation a commencé, mais sa validation finale n’a pas pu "
                "être confirmée. Contrôlez WimTech avant toute nouvelle tentative."
            ),
        }

    close_warning = None
    try:
        submit_by_id(driver, "frm:v_but_ano", min(timeout, 10))
    except Exception:
        close_warning = " La confirmation est acquise, mais la fenêtre n’a pas pu être fermée."

    return {
        "status": "ASSIGNED",
        "status_label": "Affecté",
        "pco_exists": True,
        "selected_port": port,
        "deleted_constitutions": deleted_count,
        "odf_used": used_odf,
        "cable": cable_label,
        "fibre_label": fibre_label,
        "message": f"Login {login} affecté au port {port}.{close_warning or ''}",
    }


def assign_login_to_first_port(
    *,
    config: dict,
    login: str,
    odf: str,
    zr: str,
    candidates: list[str],
    is_stopped: Callable[[], bool],
    on_log: Callable[[str, str], None],
    on_result: Callable[[int, dict], None],
    driver=None,
    initial_form_ready: bool = False,
) -> dict | None:
    """Try candidate PCOs in order and stop after the first confirmed mutation."""

    owns_driver = driver is None
    reuse_constitution_page = not owns_driver
    next_form_ready = initial_form_ready
    assigned_result = None
    halted = False
    try:
        on_log("INFO", f"Ouverture de Chrome pour l’affectation du Login {login}…")
        if owns_driver:
            driver = build_driver(bool(config.get("headless", False)), action_delay_seconds=action_delay_from_config(config))

        def test_candidate(index: int, pco: str) -> dict | None:
            nonlocal next_form_ready
            if is_stopped():
                return None
            on_log("INFO", f"Test {index + 1}/{len(candidates)} : {pco}")
            started_at = time.monotonic()
            used_pco = pco
            try:
                if reuse_constitution_page and not next_form_ready:
                    try:
                        open_add_constitution_form(
                            driver, int(config["timeout_seconds"]), delete_existing=False
                        )
                    except TimeoutException:
                        # If the preceding candidate timed out while WimTech
                        # was changing pages, restore a known state. This is a
                        # recovery path only; the normal flow keeps the same
                        # Login session and opens Ajouter directly.
                        on_log(
                            "WARNING",
                            "Page WimTech non synchronisée : reprise de la recherche du Login.",
                        )
                        prepare_pco_form(
                            driver, config, login=login, delete_existing=False,
                        )
                        next_form_ready = True

                # Consume this one-use flag before Selenium starts. If an
                # exception occurs, the following candidate must restore/open
                # the form instead of reusing the blocked page.
                current_form_ready = next_form_ready
                next_form_ready = False
                result = assign_one_pco(
                    driver,
                    config,
                    login=login,
                    odf=odf,
                    zr=zr,
                    pco=pco,
                    form_ready=current_form_ready,
                )
                fallback = alternate_prefixed_pco(pco)
                if result.get("status") == "NOT_FOUND" and fallback:
                    on_log("INFO", f"{pco} introuvable : nouvel essai avec {fallback}")
                    used_pco = fallback
                    if reuse_constitution_page:
                        open_add_constitution_form(
                            driver, int(config["timeout_seconds"]), delete_existing=False
                        )
                    result = assign_one_pco(
                        driver,
                        config,
                        login=login,
                        odf=odf,
                        zr=zr,
                        pco=fallback,
                        form_ready=reuse_constitution_page,
                    )
            except ValueError:
                # A missing/invalid Login is global to the job, not a PCO
                # condition. Let the job fail once instead of repeating the
                # same research for all candidates.
                raise
            except TimeoutException:
                # Best effort: Annuler usually brings the browser back from
                # the PCO form to the constitution page. The next iteration
                # will then click Ajouter; otherwise it uses the recovery path
                # above and researches the Login once.
                cancel_current_pco(driver, min(int(config["timeout_seconds"]), 8))
                next_form_ready = False
                result = {
                    "status": "ERROR",
                    "status_label": "Erreur",
                    "pco_exists": None,
                    "selected_port": None,
                    "message": "Délai WimTech dépassé avant le démarrage d’une mutation.",
                }
            except Exception as exc:
                result = {
                    "status": "ERROR",
                    "status_label": "Erreur",
                    "pco_exists": None,
                    "selected_port": None,
                    "message": str(exc),
                }

            result.update(
                {
                    "pco": used_pco,
                    "source_pco": pco,
                    "duration_seconds": round(time.monotonic() - started_at, 2),
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            on_result(index, result)
            level = "SUCCESS" if result["status"] == "ASSIGNED" else (
                "ERROR" if result["status"] == "MUTATION_UNKNOWN" else "INFO"
            )
            on_log(level, f"{used_pco} : {result['status_label']} - {result['message']}")
            return result

        def skip(index: int, pco: str, message: str, skipped_by: str) -> None:
            result = {
                "pco": pco,
                "status": "SKIPPED",
                "status_label": "Ignoré",
                "pco_exists": None,
                "selected_port": None,
                "duration_seconds": 0,
                "checked_at": datetime.now(timezone.utc).isoformat(),
                "skipped_by": skipped_by,
                "message": message,
            }
            on_result(index, result)
            on_log("INFO", f"{pco} ignoré : {message}")

        for group_index, (base, split_1, split_2) in enumerate(
            group_pco_candidates(candidates)
        ):
            base_index = group_index * 3
            base_result = test_candidate(base_index, base)
            if base_result is None:
                on_log("WARNING", "Affectation arrêtée par l’utilisateur.")
                break
            if base_result["status"] == "ASSIGNED":
                assigned_result = base_result
                break
            if base_result.get("halt"):
                halted = True
                break

            if base_result_confirms_existence(base_result):
                for offset, split_pco in enumerate((split_1, split_2), start=1):
                    displayed_split = (
                        alternate_prefixed_pco(split_pco)
                        if base_result.get("pco") != base
                        else split_pco
                    ) or split_pco
                    skip(
                        base_index + offset,
                        displayed_split,
                        f"{base} existe en 8 FO ; les formes /1 et /2 ne s’appliquent pas.",
                        base_result["pco"],
                    )
                continue

            for offset, split_pco in enumerate((split_1, split_2), start=1):
                split_result = test_candidate(base_index + offset, split_pco)
                if split_result is None:
                    on_log("WARNING", "Affectation arrêtée par l’utilisateur.")
                    return None
                if split_result["status"] == "ASSIGNED":
                    assigned_result = split_result
                    break
                if split_result.get("halt"):
                    return None
            if assigned_result:
                break

        if assigned_result:
            assigned_index = candidates.index(
                assigned_result.get("source_pco", assigned_result["pco"])
            )
            for index in range(assigned_index + 1, len(candidates)):
                # Do not overwrite split forms already skipped by the 8 FO rule.
                skip(
                    index,
                    candidates[index],
                    f"Affectation déjà terminée sur {assigned_result['pco']} port {assigned_result['selected_port']}.",
                    assigned_result["pco"],
                )
        elif not is_stopped() and not halted:
            on_log("WARNING", "Aucun port utilisable trouvé dans les PCO possibles.")
        return assigned_result
    finally:
        if owns_driver:
            close_driver(driver)
            on_log("INFO", "Session Chrome d’affectation fermée.")
