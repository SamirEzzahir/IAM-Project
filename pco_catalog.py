"""Project raw PCO checks into the reusable availability catalog."""

from __future__ import annotations


def _available_rows(result: dict, pco: str, ports: list[str]) -> list[dict]:
    rows = []
    for port in ports:
        rows.append({
            "pco": pco,
            "brin": str(port),
            "status": "AVAILABLE",
            "status_label": "Disponible",
            "free_ports": [str(port)],
            "free_count": 1,
            "checked_at": result.get("checked_at"),
            "source_pco": result.get("pco"),
            "message": f"Port utilisable : {port}.",
        })
    return rows


def _not_created(result: dict, pco: str) -> dict:
    return {
        "pco": pco,
        "brin": None,
        "status": "NOT_CREATED",
        "status_label": "Non créé",
        "free_ports": [],
        "free_count": 0,
        "checked_at": result.get("checked_at"),
        "source_pco": result.get("pco"),
        "message": "PCO non créé dans WimTech.",
    }


def build_pco_catalog(results: list[dict]) -> list[dict]:
    """Build one catalog row per free port or explicitly missing PCO.

    Results are expected in groups of ``base, base/1, base/2``. An existing
    eight-port base remains the base PCO and keeps its original port numbers
    from 1 to 8. Split PCO records are used only when the base is absent.
    """

    catalog: list[dict] = []
    for index in range(0, len(results) - 2, 3):
        base, split_1, split_2 = results[index:index + 3]
        base_pco = str(base.get("pco") or "")
        if not base_pco:
            continue

        if base.get("pco_exists") is True:
            base_ports: list[str] = []
            for raw_port in base.get("free_ports") or []:
                try:
                    port = int(str(raw_port))
                except ValueError:
                    continue
                if 1 <= port <= 8:
                    base_ports.append(str(port))
            catalog.extend(_available_rows(base, base_pco, base_ports))
            continue

        splits = (split_1, split_2)
        explicitly_missing = [item.get("pco_exists") is False for item in splits]
        if base.get("pco_exists") is False and all(explicitly_missing):
            catalog.append(_not_created(base, base_pco))
            continue

        for split in splits:
            split_pco = str(split.get("pco") or "")
            if not split_pco:
                continue
            if split.get("pco_exists") is True:
                catalog.extend(
                    _available_rows(split, split_pco, split.get("free_ports") or [])
                )
            elif split.get("pco_exists") is False:
                catalog.append(_not_created(split, split_pco))
    return catalog
