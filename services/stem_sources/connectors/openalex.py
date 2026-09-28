from __future__ import annotations

import os
from typing import List

from .base import BaseConnector
from ..cache import load as cache_load, save as cache_save
from ..models import EvidenceItem


class OpenAlexConnector(BaseConnector):
    source_id = "openalex"
    endpoint = "https://api.openalex.org/works"

    def available(self) -> bool:
        return bool(os.getenv("OPENALEX_API_KEY", "").strip())

    @staticmethod
    def _abstract(inv) -> str:
        if not isinstance(inv, dict) or not inv:
            return ""
        positions = []
        for word, locs in inv.items():
            for pos in locs or []:
                positions.append((int(pos), word))
        positions.sort()
        return " ".join(word for _, word in positions)

    async def search(self, query: str, limit: int = 5) -> List[EvidenceItem]:
        if not self.available():
            return []
        limit = max(1, min(25, int(limit)))
        cache_key = f"openalex:{query}:{limit}"
        cached = cache_load(cache_key, 12 * 3600)
        if cached is not None:
            return [EvidenceItem(**row) for row in cached]
        params = {
            "search": query,
            "per-page": limit,
            "api_key": os.environ["OPENALEX_API_KEY"].strip(),
        }
        r = await self._get(self.endpoint, params=params)
        data = r.json()
        out: List[EvidenceItem] = []
        for row in data.get("results", []):
            doi = row.get("doi")
            authors = [
                ((a.get("author") or {}).get("display_name") or "").strip()
                for a in (row.get("authorships") or [])
            ]
            primary = row.get("primary_location") or {}
            landing = primary.get("landing_page_url") or row.get("id") or ""
            score = row.get("relevance_score")
            try:
                relevance = max(0.50, min(1.0, float(score) / (float(score) + 20.0)))
            except Exception:
                relevance = 0.70
            out.append(EvidenceItem(
                source_id=self.source_id,
                title=row.get("display_name") or row.get("title") or "",
                url=landing,
                snippet=self._abstract(row.get("abstract_inverted_index"))[:4000],
                authors=[a for a in authors if a],
                year=row.get("publication_year"),
                doi=doi,
                external_id=row.get("id"),
                evidence_type="scholarly_index_record",
                peer_review_status="unknown",
                authority="scholarly_index",
                relevance=relevance,
                metadata={
                    "cited_by_count": row.get("cited_by_count"),
                    "type": row.get("type"),
                    "open_access": row.get("open_access"),
                },
            ))
        cache_save(cache_key, [x.to_dict() for x in out])
        return out
