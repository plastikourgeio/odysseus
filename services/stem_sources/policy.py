from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Tuple

from .language import fold_text


@dataclass(frozen=True)
class RetrievalDecision:
    """Deterministic evidence-routing decision for a STEM turn.

    The policy decides whether outside evidence is useful before an LLM is
    asked to answer. It intentionally separates official documentation,
    scholarly literature, community evidence and general web search.
    """

    external_evidence_required: bool
    official: bool = False
    scholarly: bool = False
    community: bool = False
    general_web: bool = False
    reasons: Tuple[str, ...] = ()

    def to_dict(self):
        return asdict(self)


_CURRENTNESS = re.compile(
    r"\b(latest|today|now|202[4-9]|actual|ahora|hoy|ultima|ultimo|reciente|stock|precio|price|version|firmware|release|currently)\b|\bcurrent\s+(version|release|firmware|status|availability|stock|price)\b",
    re.I,
)
_NAMED_TECH = re.compile(
    r"\b(arduino|giga\s*r1|raspberry\s*pi|esp32|micro:bit|ender\s*3|creality|prusa|bambu|"
    r"marlin|klipper|octoprint|cura|orca|bme280|sx1262|lora|gps|gnss|webots|modulino|"
    r"esp8266|espressif|python|"
    r"[a-z]+\d+[a-z0-9_.+-]*)\b",
    re.I,
)
_ELECTRICAL_SAFETY = re.compile(
    r"\b(voltage|voltaje|tension|current|corriente|amp|amper|power|alimentacion|battery|bateria|"
    r"gpio|3\.3\s*v|5\s*v|pinout|patillaje|directamente|directly)\b",
    re.I,
)
_SOURCE_REQUEST = re.compile(
    r"\b(source|sources|fuente|fuentes|evidence|evidencia|paper|papers|literature|literatura|"
    r"research|investigacion|study|estudio|verify|verifica|comprueba|confirm)\b",
    re.I,
)
_PURCHASE = re.compile(r"\b(stock|precio|price|comprar|buy|tienda|shop|disponib|availability)\b", re.I)
_COMPATIBILITY = re.compile(
    r"\b(compatib|compatible|connect|conectar|conecto|conexion|connection|bus|i2c|spi|uart|usb|pinout|patillaje|directamente|directly)\b",
    re.I,
)


def decide_retrieval(query: str, *, intent: str, domains=()) -> RetrievalDecision:
    """Return a conservative retrieval policy for one user turn.

    Basic conceptual explanations stay local. Product-specific, safety-relevant,
    troubleshooting, research, verification and current-information turns pull
    evidence before the response is composed.
    """

    text = fold_text(query)
    reasons = []
    official = scholarly = community = general = False

    named = bool(_NAMED_TECH.search(text))
    current = bool(_CURRENTNESS.search(text))
    source_request = bool(_SOURCE_REQUEST.search(text))
    electrical = bool(_ELECTRICAL_SAFETY.search(text))
    purchase = intent == "purchase" or bool(_PURCHASE.search(text))
    compatibility = bool(_COMPATIBILITY.search(text))

    if intent in {"research", "verify"} or source_request:
        scholarly = intent == "research" or any(
            d in set(domains) for d in {
                "physics", "chemistry_materials", "biology", "mathematics_statistics",
                "astronomy_space", "aerospace", "earth_science", "ai_data", "robotics",
            }
        )
        official = named or intent == "verify"
        reasons.append("explicit evidence/research request")

    if intent in {"troubleshoot", "configure", "compare", "code", "design"} and named:
        official = True
        reasons.append("named technical product/project")

    if intent in {"troubleshoot", "configure"}:
        # Community evidence is useful for symptoms/workarounds, but only after
        # official evidence and never as sole verification.
        community = True
        reasons.append("field troubleshooting evidence")

    if electrical:
        official = True
        reasons.append("electrical/safety specification")

    if named and compatibility and any(d in set(domains) for d in {"electronics", "embedded", "computing", "networking_security"}):
        official = True
        reasons.append("hardware/software compatibility specification")

    if current:
        general = True
        reasons.append("current/version-sensitive information")

    if purchase:
        general = True
        official = named
        reasons.append("live stock/price information")

    external = official or scholarly or community or general
    return RetrievalDecision(
        external_evidence_required=external,
        official=official,
        scholarly=scholarly,
        community=community,
        general_web=general,
        reasons=tuple(dict.fromkeys(reasons)),
    )
