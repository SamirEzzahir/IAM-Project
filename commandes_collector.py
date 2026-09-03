"""Collect field/ROP information from the internal Commandes application."""

from __future__ import annotations

import time
from typing import Callable

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from wimtech_checker import (
    action_delay_from_config,
    build_driver,
    close_driver,
    navigate,
    selenium_action_delay,
)


def _visible_first(driver, selectors: list[str]):
    for selector in selectors:
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            try:
                if element.is_displayed() and element.is_enabled():
                    return element
            except Exception:
                continue
    return None


def _click_text_button(driver, timeout: int, text: str):
    expected = " ".join(text.lower().split())

    def find(current):
        for button in current.find_elements(By.TAG_NAME, "button"):
            try:
                label = " ".join((button.text or "").lower().split())
                if expected in label and button.is_displayed() and button.is_enabled():
                    return button
            except Exception:
                continue
        return False

    button = WebDriverWait(driver, timeout).until(find)
    selenium_action_delay(driver)
    driver.execute_script("arguments[0].click();", button)
    return button


def authenticate_with_manual_code(
    driver,
    config: dict,
    stopped: Callable[[], bool],
    on_log: Callable[[str, str], None],
) -> None:
    """Fill saved credentials, then leave ten seconds for the temporary code."""

    timeout = int(config["timeout_seconds"])
    username = str(config.get("wiam_username") or "").strip()
    password = str(config.get("wiam_password") or "")
    if not username or not password:
        raise ValueError("Configurez le Login et le mot de passe WIAM.")

    user_field = WebDriverWait(driver, timeout).until(
        lambda current: _visible_first(
            current,
            [
                "#email", "input[name='email']", "input[autocomplete='username']",
                "input[name='username']", "input[name='login']", "#username",
                "#login", "input[type='email']",
            ],
        )
    )
    password_field = _visible_first(
        driver, ["#password", "input[name='password']", "input[type='password']"]
    )
    if not password_field:
        raise ValueError("Champ mot de passe introuvable sur la page Commandes.")

    selenium_action_delay(driver); user_field.clear(); user_field.send_keys(username)
    selenium_action_delay(driver); password_field.clear(); password_field.send_keys(password)
    submit = _visible_first(
        driver,
        ["button[type='submit']", "input[type='submit']", "button[name='login']"],
    )
    selenium_action_delay(driver)
    if submit:
        driver.execute_script("arguments[0].click();", submit)
    else:
        password_field.send_keys(Keys.ENTER)

    on_log("WARNING", "Saisissez maintenant le code temporaire dans Chrome : 10 secondes.")
    for remaining in range(10, 0, -1):
        if stopped():
            return
        on_log("INFO", f"Code temporaire : {remaining} seconde(s) restante(s).")
        time.sleep(1)


def _wait_search(driver, timeout: int):
    return WebDriverWait(driver, timeout).until(
        lambda current: _visible_first(current, ["input[name='search']"])
    )


def _extract_terrain(driver, timeout: int) -> dict[str, str]:
    def find_card(current):
        for heading in current.find_elements(By.XPATH, "//h5"):
            if "INFORMATIONS TERRAIN" in (heading.text or "").upper():
                return heading.find_element(By.XPATH, "ancestor::div[contains(@class,'mt-4')][1]")
        return False

    card = WebDriverWait(driver, timeout).until(find_card)
    values: dict[str, str] = {}
    for row in card.find_elements(By.XPATH, ".//div[contains(@class,'justify-between')]"):
        spans = row.find_elements(By.TAG_NAME, "span")
        if len(spans) >= 2:
            label = " ".join((spans[0].text or "").strip().split())
            value = " ".join((spans[-1].text or "").strip().split())
            if label:
                values[label.lower()] = value
    return {
        "nom_pco": values.get("nom pco", ""),
        "port_pco": values.get("port pco", ""),
        "modele_ont": values.get("modèle ont", values.get("modele ont", "")),
        "nom_splitter": values.get("nom splitter", ""),
        "client_contacte": values.get("client contacté", values.get("client contacte", "")),
        "distance_branchement": values.get("distance branchement (m)", ""),
    }


def collect_one_command(driver, config: dict, command: str) -> dict[str, str]:
    timeout = int(config["timeout_seconds"])
    search = _wait_search(driver, timeout)
    selenium_action_delay(driver); search.clear(); search.send_keys(command)

    # React filters automatically. Avoid clicking a button left over from the
    # preceding CMD before the new result has had time to render.
    time.sleep(0.7)
    view_button = WebDriverWait(driver, timeout).until(
        lambda current: next(
            (
                button for button in current.find_elements(By.CSS_SELECTOR, "button[title='Voir details']")
                if button.is_displayed() and button.is_enabled()
                and command.upper() in (current.find_element(By.TAG_NAME, "body").text or "").upper()
            ),
            False,
        )
    )
    selenium_action_delay(driver); driver.execute_script("arguments[0].click();", view_button)
    handles_before = set(driver.window_handles)
    _click_text_button(driver, timeout, "Ouvrir le suivi workflow")

    WebDriverWait(driver, timeout).until(
        lambda current: len(current.window_handles) > len(handles_before)
        or any("Validation" in (button.text or "") for button in current.find_elements(By.TAG_NAME, "button"))
    )
    new_handles = [handle for handle in driver.window_handles if handle not in handles_before]
    if new_handles:
        driver.switch_to.window(new_handles[-1])
    _click_text_button(driver, timeout, "Validation")
    return _extract_terrain(driver, timeout)


def run_commandes_collection(
    *,
    config: dict,
    rows: list[dict],
    stopped: Callable[[], bool],
    on_result: Callable[[int, dict], None],
    on_log: Callable[[str, str], None],
) -> None:
    url = str(config.get("commandes_url") or "").strip()
    if not url:
        raise ValueError("Configurez l'URL de l'application Commandes.")
    driver = build_driver(
        False,  # The temporary code must always be entered manually.
        ignore_certificate_errors=True,
        action_delay_seconds=action_delay_from_config(config),
    )
    try:
        navigate(driver, url)
        authenticate_with_manual_code(driver, config, stopped, on_log)
        if stopped():
            return
        # The identity provider does not automatically return to the original
        # protected route after the temporary code is accepted.
        on_log("INFO", "Code temporaire terminé : ouverture de la page Commandes.")
        navigate(driver, url)
        _wait_search(driver, int(config["timeout_seconds"]))
        on_log("SUCCESS", "Authentification terminée. Début de la collecte CMD.")
        for index, row in enumerate(rows):
            if stopped():
                break
            started = time.monotonic()
            result = {**row, "nom_splitter": "", "port_pco": "", "nom_pco": "", "modele_ont": "", "client_contacte": "", "distance_branchement": ""}
            try:
                # Return to the list after every workflow/modal while keeping
                # the authenticated Chrome session.
                navigate(driver, url)
                result.update(collect_one_command(driver, config, row["cmd"]))
                result.update(status="COMPLETED", status_label="Terminé", message="Informations terrain collectées.")
            except TimeoutException:
                result.update(status="ERROR", status_label="Introuvable", message="CMD, workflow Validation ou informations terrain introuvables.")
                navigate(driver, url)
                _wait_search(driver, int(config["timeout_seconds"]))
            except Exception as exc:
                result.update(status="ERROR", status_label="Erreur", message=str(exc))
                navigate(driver, url)
                _wait_search(driver, int(config["timeout_seconds"]))
            result["duration_seconds"] = round(time.monotonic() - started, 2)
            on_result(index, result)
            on_log("SUCCESS" if result["status"] == "COMPLETED" else "ERROR", f"{row['cmd']} : {result['message']}")
    finally:
        close_driver(driver)
