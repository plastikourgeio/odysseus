from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Iterable, Optional, Tuple
from urllib.parse import urlsplit

from .language import fold_text


@dataclass(frozen=True)
class OfficialRoute:
    """Deterministic official-documentation search route.

    ``domains`` are the only hosts accepted as proof that the constrained
    search actually stayed on the intended primary documentation family.
    """

    source_id: str
    label: str
    domains: Tuple[str, ...]
    reason: str

    def to_dict(self):
        return asdict(self)


# Ordered from specific product ecosystems to more generic developer docs.
# Keep this deterministic: no LLM/API call is needed to decide the route.
_RULES = [
    (
        re.compile(r"\b(arduino|giga\s*r1|modulino|portenta|nicla|uno\s*r4|nano\s*(33|esp32)?|mega\s*2560)\b", re.I),
        OfficialRoute(
            "arduino_docs",
            "Arduino Documentation",
            ("docs.arduino.cc", "support.arduino.cc"),
            "Arduino board/module named in the query",
        ),
    ),
    (
        re.compile(r"\b(raspberry\s*pi|raspberry\s*pi\s*pico|rp2040|rp2350)\b", re.I),
        OfficialRoute(
            "raspberry_pi_docs",
            "Raspberry Pi Documentation",
            ("raspberrypi.com",),
            "Raspberry Pi hardware/software named in the query",
        ),
    ),
    (
        re.compile(r"\b(esp32|esp8266|espressif|esp-idf|idf.py)\b", re.I),
        OfficialRoute(
            "espressif_docs",
            "Espressif Documentation",
            ("docs.espressif.com",),
            "Espressif device/toolchain named in the query",
        ),
    ),
    (
        re.compile(r"\b(micro:?bit|microbit|makecode)\b", re.I),
        OfficialRoute(
            "microbit_docs",
            "micro:bit Documentation",
            ("tech.microbit.org", "microbit.org", "makecode.microbit.org"),
            "micro:bit ecosystem named in the query",
        ),
    ),
    (
        re.compile(r"\b(ender\s*3|ender\s*5|creality|k1\s*(max|c)?\b|halot)\b", re.I),
        OfficialRoute(
            "creality_docs",
            "Creality Documentation",
            ("wiki.creality.com", "creality.com"),
            "Creality printer named in the query",
        ),
    ),
    (
        re.compile(r"\b(prusa|mk3s?|mk4s?|mini\+?|xl\b)\b", re.I),
        OfficialRoute(
            "prusa_docs",
            "Prusa Knowledge Base",
            ("help.prusa3d.com", "prusa3d.com"),
            "Prusa printer/ecosystem named in the query",
        ),
    ),
    (
        re.compile(r"\b(bambu|bambu\s*lab|x1c?|p1[ps]?|a1\s*mini)\b", re.I),
        OfficialRoute(
            "bambu_docs",
            "Bambu Lab Wiki",
            ("wiki.bambulab.com", "bambulab.com"),
            "Bambu Lab printer named in the query",
        ),
    ),
    (
        re.compile(r"\b(klipper)\b", re.I),
        OfficialRoute(
            "klipper_docs",
            "Klipper Documentation",
            ("klipper3d.org",),
            "Klipper firmware named in the query",
        ),
    ),
    (
        re.compile(r"\b(marlin\s*(firmware)?)\b", re.I),
        OfficialRoute(
            "marlin_docs",
            "Marlin Firmware Documentation",
            ("marlinfw.org",),
            "Marlin firmware named in the query",
        ),
    ),
    (
        re.compile(r"\b(octoprint)\b", re.I),
        OfficialRoute(
            "octoprint_docs",
            "OctoPrint Documentation",
            ("docs.octoprint.org", "octoprint.org"),
            "OctoPrint named in the query",
        ),
    ),
    (
        re.compile(r"\b(webots|cyberbotics)\b", re.I),
        OfficialRoute(
            "webots_docs",
            "Webots / Cyberbotics Documentation",
            ("cyberbotics.com",),
            "Webots named in the query",
        ),
    ),
    (
        re.compile(r"\b(python|cpython|pip|venv|asyncio)\b", re.I),
        OfficialRoute(
            "python_docs",
            "Python Documentation",
            ("docs.python.org",),
            "Python language/runtime named in the query",
        ),
    ),
]


def resolve_official_routes(query: str, *, max_routes: int = 2) -> Tuple[OfficialRoute, ...]:
    """Return known official documentation families mentioned by the query."""
    text = fold_text(query)
    out = []
    seen = set()
    for pattern, route in _RULES:
        if pattern.search(text) and route.source_id not in seen:
            out.append(route)
            seen.add(route.source_id)
            if len(out) >= max(1, int(max_routes)):
                break
    return tuple(out)


def build_official_search_query(query: str, routes: Iterable[OfficialRoute]) -> str:
    """Constrain an existing search query to known official documentation hosts."""
    routes = tuple(routes)
    domains = []
    for route in routes:
        for domain in route.domains:
            if domain not in domains:
                domains.append(domain)
    if not domains:
        return query.strip()
    site_clause = " OR ".join(f"site:{d}" for d in domains)
    return f"({site_clause}) {query.strip()}".strip()


def url_matches_official_routes(url: str, routes: Iterable[OfficialRoute]) -> bool:
    """True when URL host is one of the resolved official hosts/subdomains."""
    try:
        host = (urlsplit(url).hostname or "").casefold().rstrip(".")
    except Exception:
        return False
    if not host:
        return False
    for route in routes:
        for domain in route.domains:
            d = domain.casefold().rstrip(".")
            if host == d or host.endswith("." + d):
                return True
    return False


def describe_routes(routes: Iterable[OfficialRoute]) -> str:
    labels = []
    for route in routes:
        if route.label not in labels:
            labels.append(route.label)
    return ", ".join(labels)
