from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

from .language import fold_text

_SPANISH_STOPWORDS = {
    "a", "al", "algo", "ante", "con", "contra", "cual", "cuando", "de", "del", "desde", "donde",
    "el", "ella", "ellas", "ellos", "en", "entre", "era", "es", "esa", "ese", "eso", "esta", "este",
    "esto", "hacer", "hasta", "hay", "la", "las", "le", "lo", "los", "mas", "me", "mi", "mis", "muy",
    "necesito", "no", "o", "para", "pero", "por", "porque", "puede", "puedo", "que", "quiero", "se",
    "si", "sin", "sobre", "su", "sus", "tengo", "tiene", "un", "una", "unos", "unas", "y", "ya",
}

_KNOWN_TECH_ENTITY_WORDS = {
    "arduino", "giga", "modulino", "raspberry", "pi", "esp32", "stm32", "micro:bit",
    "ender", "creality", "prusa", "bambu", "marlin", "klipper", "octoprint", "cura", "orca",
    "webots", "bme280", "sx1262", "lora", "gps", "gnss", "petg", "pla", "abs", "nylon",
    "robot", "robotics", "stem",
}

_INTENT_HINTS = {
    "troubleshoot": "troubleshooting",
    "purchase": "buy stock price",
    "configure": "settings configuration",
    "compare": "comparison",
    "research": "research",
    "code": "code programming",
    "design": "design architecture",
    "brainstorm": "design ideas",
    "explain": "explanation",
}

_DOMAIN_HINTS = {
    "electronics": "electronics",
    "embedded": "embedded systems",
    "computing": "computing",
    "networking_security": "networking security",
    "robotics": "robotics",
    "fabrication": "3D printing",
    "mechanical_engineering": "mechanical engineering",
    "physics": "physics",
    "chemistry_materials": "materials science",
    "biology": "biology",
    "mathematics_statistics": "mathematics statistics",
    "astronomy_space": "astronomy space",
    "aerospace": "aerospace engineering",
    "earth_science": "earth science",
    "ai_data": "artificial intelligence data",
    "measurement": "measurement calibration",
    "engineering_design": "engineering design",
}


@lru_cache(maxsize=1)
def _terms() -> List[dict]:
    path = Path(__file__).with_name("technical_terms.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return list(raw.get("terms", []))


def matched_terms(text: str) -> List[dict]:
    folded = f" {fold_text(text)} "
    out: List[dict] = []
    seen = set()
    for term in _terms():
        aliases = list(term.get("es", [])) + list(term.get("en", [])) + [term.get("canonical", "")]
        for alias in aliases:
            a = fold_text(alias).strip()
            if not a:
                continue
            # Flexible whitespace but avoid matching inside longer alphanumeric tokens.
            pattern = r"(?<![a-z0-9])" + re.escape(a).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
            if re.search(pattern, folded):
                key = term.get("canonical", "").casefold()
                if key not in seen:
                    seen.add(key)
                    out.append(term)
                break
    return out


def canonical_terms(text: str) -> List[str]:
    return [t.get("canonical", "") for t in matched_terms(text) if t.get("canonical")]


def _preserve_identifiers(text: str) -> List[str]:
    """Keep product/model identifiers and common technical tokens from the original prompt."""
    tokens = re.findall(r"\b[A-Za-z][A-Za-z0-9_.+-]*(?:\s+[A-Za-z]*\d[A-Za-z0-9_.+-]*)?\b", text or "")
    out: List[str] = []
    seen = set()
    for tok in tokens:
        folded = fold_text(tok).strip()
        if not folded or folded in _SPANISH_STOPWORDS:
            continue
        interesting = (
            any(ch.isdigit() for ch in tok)
            or tok.isupper()
            or any(ch in tok for ch in "+-_.")
            or folded in _KNOWN_TECH_ENTITY_WORDS
        )
        if interesting and folded not in seen:
            seen.add(folded)
            out.append(tok.strip())
    return out


def build_english_technical_query(query: str, *, intent: str, domains: Sequence[str]) -> Tuple[str, Tuple[str, ...]]:
    """
    Build a search-oriented English technical query without an LLM.

    This is not machine translation. It extracts recognized STEM concepts,
    preserves product identifiers/acronyms, and adds minimal intent/domain
    context. Unknown user wording remains available through the original query
    as a second retrieval variant.
    """
    terms = canonical_terms(query)
    identifiers = _preserve_identifiers(query)

    parts: List[str] = []
    seen = set()

    def add(value: str) -> None:
        v = (value or "").strip()
        key = fold_text(v)
        if v and key not in seen:
            seen.add(key)
            parts.append(v)

    for value in identifiers:
        add(value)
    for value in terms:
        add(value)

    if intent in _INTENT_HINTS:
        add(_INTENT_HINTS[intent])
    # Add at most two broad domain hints to avoid over-constraining search.
    added = 0
    for domain in domains:
        hint = _DOMAIN_HINTS.get(domain)
        if hint:
            add(hint)
            added += 1
            if added >= 2:
                break

    # If the glossary recognizes very little, retain informative non-stopword
    # tokens so the query still carries product/project context.
    if len(parts) < 3:
        for token in re.findall(r"[A-Za-zÀ-ÿ0-9_+#.-]+", query or ""):
            folded = fold_text(token)
            if len(folded) < 2 or folded in _SPANISH_STOPWORDS:
                continue
            add(token)
            if len(parts) >= 8:
                break

    return " ".join(parts).strip(), tuple(terms)
