"""Small JSON configuration store for the local Selenium application."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "data" / "config.json"
CONFIG_LOCK = Lock()

DEFAULT_CONFIG = {
    "wimtech_url": (
        "http://wimtech/Mutation/mutationIndividuelleGPON.jsf?"
        "a=PFPOTT%5D%5CG&b=Nnuq}o7-./1&load=1"
    ),
    "test_login": "I10260472",
    "timeout_seconds": 20,
    "headless": False,
    "wiam_url": "",
    "wiam_username": "",
    "wiam_password": "",
}


def load_config() -> dict:
    with CONFIG_LOCK:
        if not CONFIG_PATH.exists():
            return dict(DEFAULT_CONFIG)
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return dict(DEFAULT_CONFIG)

    result = dict(DEFAULT_CONFIG)
    if isinstance(saved, dict):
        result.update({key: saved[key] for key in DEFAULT_CONFIG if key in saved})
    return result


def save_config(payload: dict) -> dict:
    current = load_config()
    url = str(payload.get("wimtech_url", current["wimtech_url"])).strip()
    login = str(payload.get("test_login", current["test_login"])).strip()

    if not url.startswith(("http://", "https://")):
        raise ValueError("L’URL WimTech doit commencer par http:// ou https://")
    if not login:
        raise ValueError("Le Login de test est obligatoire.")

    try:
        timeout = int(payload.get("timeout_seconds", current["timeout_seconds"]))
    except (TypeError, ValueError) as exc:
        raise ValueError("Le délai doit être un nombre entier.") from exc
    if timeout < 5 or timeout > 120:
        raise ValueError("Le délai doit être compris entre 5 et 120 secondes.")

    wiam_url = str(payload.get("wiam_url", current["wiam_url"])).strip()
    if wiam_url and not wiam_url.startswith(("http://", "https://")):
        raise ValueError("L’URL WIAM doit commencer par http:// ou https://")
    wiam_password = str(payload.get("wiam_password", "")).strip() or current["wiam_password"]
    result = {
        "wimtech_url": url,
        "test_login": login,
        "timeout_seconds": timeout,
        "headless": bool(payload.get("headless", current["headless"])),
        "wiam_url": wiam_url,
        "wiam_username": str(payload.get("wiam_username", current["wiam_username"])).strip(),
        "wiam_password": wiam_password,
    }

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_PATH.with_suffix(".tmp")
    with CONFIG_LOCK:
        temporary.write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(CONFIG_PATH)
    return result
