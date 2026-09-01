"""SPL parsing and PCO candidate generation for FB EMM."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass


SPL_PATTERN = re.compile(
    r"^(?P<odf>[^\s-]+)-(?P<zone>[^\s-]+)-"
    r"(?P<chassis>\d)(?P<baie>\d)(?P<card>\d)(?P<alternate>1)?\."
    r"(?P<port>\d{1,2})$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SplResult:
    odf: str
    zr: str
    spl: str
    chassis: int
    baie: int
    card: int
    port: int
    pco_bases: list[str]
    pco_candidates: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def parse_spl(value: str) -> SplResult:
    """Parse an SPL and return every possible PCO ticketing form.

    Example: ``OFAD33-ZO-113.16`` generates the four base positions and
    their base, /1 and /2 forms, for a total of 12 candidates. Some WimTech
    data uses an additional trailing ``1`` in the equipment segment, such as
    ``OFBT03-ZO-1111.3``; it is treated exactly like ``OFBT03-ZO-111.3``.
    """

    spl = str(value or "").strip().upper()
    match = SPL_PATTERN.fullmatch(spl)
    if not match:
        raise ValueError(
            "Format SPL invalide. Exemples attendus : "
            "OFAD33-ZO-113.16 ou OFBT03-ZO-1111.3"
        )

    odf = match.group("odf")
    zone = match.group("zone")
    chassis = int(match.group("chassis"))
    baie = int(match.group("baie"))
    card = int(match.group("card"))
    port = int(match.group("port"))

    if chassis not in (1, 2):
        raise ValueError("Le châssis GPON doit être 1 ou 2.")
    if baie not in (1, 2):
        raise ValueError("La baie GPON doit être 1 ou 2.")
    if card < 1 or card > 9:
        raise ValueError("La carte GPON doit être comprise entre 1 et 9.")
    if port < 1 or port > 16:
        raise ValueError("Le port GPON doit être compris entre 1 et 16.")

    zr = f"{odf}-{zone}"
    # Equipment position 121 uses the baie/card prefix followed by a dot in
    # WimTech, independently of the ODF name (121.14 -> 21.14...).
    prefix = f"{baie}{card}." if (chassis, baie, card) == (1, 2, 1) else (
        "" if card == 1 else str(card)
    )
    pco_bases = [
        f"{prefix}{port}11",
        f"{prefix}{port}12",
        f"{prefix}{port}21",
        f"{prefix}{port}22",
    ]
    pco_candidates = [
        candidate
        for base in pco_bases
        for candidate in (f"{zr}-{base}", f"{zr}-{base}/1", f"{zr}-{base}/2")
    ]

    return SplResult(
        odf=odf,
        zr=zr,
        spl=spl,
        chassis=chassis,
        baie=baie,
        card=card,
        port=port,
        pco_bases=pco_bases,
        pco_candidates=pco_candidates,
    )


def alternate_prefixed_pco(pco: str) -> str | None:
    """Return WimTech's ``T.`` alias for a baie/card-prefixed PCO name."""

    value = str(pco or "").strip().upper()
    if "-" not in value:
        return None
    location, suffix = value.rsplit("-", 1)
    match = re.fullmatch(r"\d{2}\.(.+)", suffix)
    if not match:
        return None
    return f"{location}-T.{match.group(1)}"


def group_pco_candidates(candidates: list[str]) -> list[tuple[str, str, str]]:
    """Group candidates as ``base, base/1, base/2``.

    The generator always returns this order. Keeping the validation here makes
    the Selenium optimization safe: split forms are skipped only inside their
    own confirmed base group.
    """

    if len(candidates) % 3:
        raise ValueError("La liste PCO doit contenir des groupes base, /1 et /2.")

    groups: list[tuple[str, str, str]] = []
    for index in range(0, len(candidates), 3):
        base, split_1, split_2 = candidates[index:index + 3]
        if split_1 != f"{base}/1" or split_2 != f"{base}/2":
            raise ValueError(f"Groupe PCO invalide autour de {base}.")
        groups.append((base, split_1, split_2))
    return groups
