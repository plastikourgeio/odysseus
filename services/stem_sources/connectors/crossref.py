from __future__ import annotations

import os
import re
from typing import List

from .base import BaseConnector
from ..cache import load as cache_load, save as cache_save
from ..models import EvidenceItem


_GENERIC = {
    "research", "study", "studies", "investigation", "investigacion",
    "about", "sobre", "into", "the", "and", "de", "la", "el", "en",
    "robotics",  # domain hint; the specific robot/avoidance terms carry more signal
}


def _tokens(value: str) -> list[str]:
    return [
        t for t in re.findall(r"[a-z0-9]+", (value or "").casefold())
        if len(t) > 2 and t not in _GENERIC
    ]


def _lexical_score(query: str, title: str, container: str) -> float:
    q = set(_tokens(query))
    if not q:
        return 0.0
    hay = set(_tokens(f"{title} {container}"))
    overlap = len(q & hay) / max(1, len(q))

    # Education context is semantically important in STEM-learning searches.
    education_terms = {"education", "educational", "teaching", "learning", "stem"}
    education_requested = bool(q & education_terms)
    education_present = bool(hay & education_terms)
    if education_requested:
        overlap += 0.25 if education_present else -0.12

    return max(0.0, min(1.0, overlap))


class CrossrefConnector(BaseConnector):
    source_id = "crossref"
    endpoint = "https://api.crossref.org/works"

    def headers(self):
        h = super().headers()
        contact = os.getenv("STEM_CONTACT_EMAIL", "").strip()
        if contact:
            h["User-Agent"] = f"OdysseusSTEM/0.8 (mailto:{contact})"
        return h

    async def search(self, query: str, limit: int = 5) -> List[EvidenceItem]:
        limit = max(1, min(20, int(limit)))
        cache_key = f"crossref-v08:{query}:{limit}"
        cached = cache_load(cache_key, 24 * 3600)
        if cached is not None:
            return [EvidenceItem(**row) for row in cached]

        # Crossref ranking is broad bibliographic relevance. Pull a wider single
        # page, then rerank locally for the actual STEM query. This costs one API
        # call but avoids returning generic records such as "COLLISION AVOIDANCE"
        # ahead of records that preserve an education/robotics context.
        rows = min(100, max(20, limit * 8))
        params = {"query.bibliographic": query, "rows": rows}
        contact = os.getenv("STEM_CONTACT_EMAIL", "").strip()
        if contact:
            params["mailto"] = contact

        r = await self._get(self.endpoint, params=params)
        items = ((r.json().get("message") or {}).get("items") or [])
        candidates: List[EvidenceItem] = []
        for row in items:
            title_list = row.get("title") or []
            title = title_list[0] if title_list else ""
            container = (row.get("container-title") or [""])[0] or ""
            author_names = []
            for a in row.get("author") or []:
                author_names.append(" ".join(x for x in [a.get("given"), a.get("family")] if x))
            issued = ((row.get("issued") or {}).get("date-parts") or [[]])
            year = issued[0][0] if issued and issued[0] else None

            provider_score = row.get("score")
            try:
                provider_norm = max(0.0, min(1.0, float(provider_score) / 100.0))
            except Exception:
                provider_norm = 0.5
            lexical = _lexical_score(query, title, container)
            relevance = max(0.0, min(1.0, (lexical * 0.72) + (provider_norm * 0.28)))

            doi = row.get("DOI")
            candidates.append(EvidenceItem(
                source_id=self.source_id,
                title=title,
                url=row.get("URL") or (f"https://doi.org/{doi}" if doi else ""),
                snippet=container,
                authors=[a for a in author_names if a],
                year=year,
                doi=doi,
                external_id=doi,
                evidence_type="bibliographic_metadata",
                peer_review_status="unknown",
                authority="scholarly_index",
                relevance=relevance,
                metadata={
                    "type": row.get("type"),
                    "publisher": row.get("publisher"),
                    "container_title": container,
                    "relation": row.get("relation"),
                    "update_to": row.get("update-to"),
                    "crossref_score": provider_score,
                    "local_lexical_score": lexical,
                    "evidence_scope": "bibliographic_only",
                    "content_available": False,
                },
            ))

        candidates.sort(key=lambda item: item.relevance, reverse=True)
        out = candidates[:limit]
        cache_save(cache_key, [x.to_dict() for x in out])
        return out
