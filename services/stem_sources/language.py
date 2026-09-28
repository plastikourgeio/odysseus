from __future__ import annotations

import re
import unicodedata
from typing import Iterable

_SPANISH_MARKERS = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "que", "como",
    "por", "para", "con", "sin", "mi", "mis", "tengo", "tiene", "tienen", "quiero", "necesito",
    "puedo", "puede", "podria", "podemos", "cual", "cuando", "donde", "porque", "porqué", "por que",
    "no", "funciona", "falla", "fallo", "problema", "ayuda", "hacer", "sirve", "cambiar", "primero",
    "esta", "este", "estos", "estas", "esto", "desde", "hasta", "entre", "sobre", "mas", "menos",
}

_ENGLISH_MARKERS = {
    "the", "a", "an", "of", "to", "and", "or", "with", "without", "my", "i", "want", "need",
    "can", "could", "which", "what", "when", "where", "why", "how", "not", "working", "fails", "failed",
    "problem", "help", "make", "build", "change", "first", "this", "that", "from", "into", "between",
    "about", "more", "less", "does", "do", "is", "are", "it", "for", "on", "in",
}


def fold_text(value: str) -> str:
    """Lower-case text and remove accents for robust rule matching."""
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return value.casefold()


def _words(text: str) -> Iterable[str]:
    return re.findall(r"[a-zA-ZÀ-ÿ0-9_+#.-]+", text or "")


def detect_language(text: str) -> str:
    """
    Lightweight Spanish/English detector for technical prompts.

    Returns one of: ``es``, ``en``, ``mixed`` or ``unknown``.
    Product names and technical acronyms are deliberately ignored as language
    signals. The goal is deterministic routing without spending an LLM call.
    """
    raw = text or ""
    folded = fold_text(raw)
    words = [fold_text(w) for w in _words(raw)]

    es = sum(1 for w in words if w in _SPANISH_MARKERS)
    en = sum(1 for w in words if w in _ENGLISH_MARKERS)

    # Spanish punctuation and common interrogatives are strong signals.
    if "¿" in raw or "¡" in raw:
        es += 2
    for token in (" que ", " cual ", " como ", " donde ", " por que ", " necesito ", " quiero "):
        if token in f" {folded} ":
            es += 1

    if es == 0 and en == 0:
        return "unknown"
    if es >= max(2, en * 2):
        return "es"
    if en >= max(2, es * 2):
        return "en"
    if es and en:
        return "mixed"
    return "es" if es else "en"


def preferred_response_language(text: str, default: str = "es") -> str:
    lang = detect_language(text)
    if lang == "en":
        return "en"
    if lang in {"es", "mixed"}:
        return "es"
    return default
