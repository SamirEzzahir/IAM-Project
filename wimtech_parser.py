"""Pure text parsing helpers for WimTech GPON result pages."""

from __future__ import annotations

import re
import unicodedata


ACTIVE_CABLE_PATTERN = re.compile(r"\(FO(?:4|8)-ACTIVE\)", re.IGNORECASE)
FIBRE_PORT_PATTERN = re.compile(r"^\s*(\d+)\s*\(FIBRE-", re.IGNORECASE)


def normalize(value: str) -> str:
    text = unicodedata.normalize("NFD", str(value or ""))
    text = "".join(character for character in text if unicodedata.category(character) != "Mn")
    text = text.replace("'", " ").replace("’", " ")
    return " ".join(text.upper().split())


def is_active_fo_cable(value: str) -> bool:
    """True for WimTech cable labels such as (FO4-Active) or (FO8-Active)."""

    return bool(ACTIVE_CABLE_PATTERN.search(normalize(value)))


def extract_available_fibre_ports(labels: list[str]) -> list[str]:
    """Return ports that are Libre or Active/En cours decon.

    Occupied examples such as ``FIBRE-Active ... (En service)`` and
    ``FIBRE-En cours ... (En cours)`` are intentionally excluded.
    """

    ports: set[int] = set()
    for label in labels:
        normalized = normalize(label)
        available = "(FIBRE-LIBRE)" in normalized or "EN COURS DECON" in normalized
        if not available:
            continue

        match = FIBRE_PORT_PATTERN.search(normalized)
        if match:
            ports.add(int(match.group(1)))

    return [str(port) for port in sorted(ports)]


def usable_fibre_port(value: str) -> str | None:
    """Return the port number when a fibre label can receive a mutation."""

    ports = extract_available_fibre_ports([value])
    return ports[0] if ports else None


def parse_fibre_label(value: str) -> dict | None:
    """Extract the port, fibre state and current Login from a tree label."""

    text = " ".join(str(value or "").split())
    port_match = FIBRE_PORT_PATTERN.search(text)
    state_match = re.search(r"\(FIBRE-([^)]+)\)", text, re.IGNORECASE)
    if not port_match or not state_match:
        return None

    remainder = text[state_match.end():].strip().lstrip(",").strip()
    service_state = None
    trailing_state = re.search(r"\(([^()]*)\)\s*$", remainder)
    if trailing_state:
        service_state = trailing_state.group(1).strip() or None
        remainder = remainder[:trailing_state.start()].strip().rstrip(",").strip()

    return {
        "port": str(int(port_match.group(1))),
        "fibre_state": state_match.group(1).strip(),
        "current_login": remainder or None,
        "service_state": service_state,
    }


def is_equipment_missing(value: str) -> bool:
    return "PAS D EQUIPEMENT INSTALLE AU NIVEAU DE CETTE GEOLOCALISATION" in normalize(value)


def base_result_confirms_existence(result: dict) -> bool:
    """Only a confirmed existing base PCO may suppress its /1 and /2 tests."""

    return result.get("pco_exists") is True
