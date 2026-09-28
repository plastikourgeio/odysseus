from __future__ import annotations

import asyncio
import re
import time
from typing import List

from .base import BaseConnector
from ..cache import load as cache_load, save as cache_save
from ..models import EvidenceItem


class PlosConnector(BaseConnector):
    source_id = "plos"
    endpoint = "https://api.plos.org/search"
    _lock = asyncio.Lock()
    _last_call = 0.0

    async def _respect_rate_limit(self) -> None:
        # PLOS documents a maximum of 10 search API requests per minute.
        async with self._lock:
            remaining = 6.1 - (time.monotonic() - self.__class__._last_call)
            if remaining > 0:
                await asyncio.sleep(remaining)
            self.__class__._last_call = time.monotonic()

    @staticmethod
    def _solr_escape(value: str) -> str:
        # Escape common Solr special characters while preserving spaces.
        return re.sub(r'([+\-!(){}\[\]^"~*?:\\/])', r'\\\1', value)

    async def search(self, query: str, limit: int = 5) -> List[EvidenceItem]:
        limit = max(1, min(20, int(limit)))
        cache_key = f"plos:{query}:{limit}"
        cached = cache_load(cache_key, 24 * 3600)
        if cached is not None:
            return [EvidenceItem(**row) for row in cached]
        await self._respect_rate_limit()
        params = {
            "q": f"everything:{self._solr_escape(query)}",
            "rows": limit,
            "wt": "json",
            "fl": "id,title_display,abstract,author_display,publication_date,journal,article_type",
        }
        r = await self._get(self.endpoint, params=params)
        docs = ((r.json().get("response") or {}).get("docs") or [])
        out: List[EvidenceItem] = []
        for idx, row in enumerate(docs):
            doi = row.get("id")
            abstract = row.get("abstract") or []
            if isinstance(abstract, list):
                abstract = " ".join(str(x) for x in abstract)
            authors = row.get("author_display") or []
            if isinstance(authors, str):
                authors = [authors]
            date = str(row.get("publication_date") or "")
            year = int(date[:4]) if date[:4].isdigit() else None
            out.append(EvidenceItem(
                source_id=self.source_id,
                title=row.get("title_display") or "",
                url=f"https://doi.org/{doi}" if doi else "",
                snippet=str(abstract),
                authors=[str(a) for a in authors],
                year=year,
                doi=doi,
                external_id=doi,
                evidence_type="peer_reviewed_article",
                peer_review_status="peer_reviewed",
                authority="peer_reviewed",
                relevance=max(0.60, 1.0 - idx * 0.05),
                metadata={"journal": row.get("journal"), "article_type": row.get("article_type")},
            ))
        cache_save(cache_key, [x.to_dict() for x in out])
        return out
