from __future__ import annotations

import re


# Search-engine syntax that is useful to Brave/Tavily but harmful when passed
# verbatim to structured scholarly APIs such as arXiv/Crossref.
_WEB_DIRECTIVE = re.compile(
    r"(?<!\w)(?:site|filetype|inurl|intitle|intext):(?:\"[^\"]+\"|'[^']+'|[^\s()]+)",
    re.I,
)
_BOOLEAN = re.compile(r"\b(?:AND|OR|NOT)\b", re.I)
_PUBLICATION_FILTER = re.compile(
    r"\b(?:papers?|proceedings?|journals?|articles?|publications?|doi)\b",
    re.I,
)


def normalize_scholarly_query(query: str) -> str:
    """Turn an Agent/web-search-shaped query into a scholarly topic query.

    Agent models often emit queries such as::

        site:arxiv.org "obstacle avoidance" ("educational robotics" OR "STEM")

    Those operators are meaningful to a web search engine but become literal
    AND terms in structured APIs.  Strip only transport/source-shape syntax and
    generic publication words; preserve the scientific topic phrases.
    """
    text = " ".join(str(query or "").split()).strip()
    if not text:
        return ""

    text = _WEB_DIRECTIVE.sub(" ", text)
    text = _BOOLEAN.sub(" ", text)
    text = _PUBLICATION_FILTER.sub(" ", text)
    text = text.replace("(", " ").replace(")", " ")
    text = re.sub(r"\s+", " ", text).strip(" ,;:-")

    # If educational-robotics wording is already explicit, a trailing quoted
    # STEM synonym from a web OR-clause only over-constrains structured APIs.
    folded = text.casefold()
    if "educational robotics" in folded or "robotics education" in folded:
        text = re.sub(r'\s*["\']?STEM["\']?\s*', " ", text, flags=re.I)
        text = re.sub(r"\s+", " ", text).strip()

    return text
