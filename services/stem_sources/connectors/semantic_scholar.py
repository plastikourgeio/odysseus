from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import List

import httpx

from .base import BaseConnector, ProviderUnavailable
from ..cache import load as cache_load, save as cache_save
from ..models import EvidenceItem

logger = logging.getLogger(__name__)


class SemanticScholarConnector(BaseConnector):
    source_id = "semantic_scholar"
    endpoint = "https://api.semanticscholar.org/graph/v1/paper/search"

    # A public unauthenticated request shares a global throttle pool.  Once that
    # pool returns 429, repeated immediate requests are counterproductive.
    _pace_lock = asyncio.Lock()
    _last_call = 0.0
    _cooldown_until = 0.0

    def __init__(self) -> None:
        self.api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "").strip()
        # Authenticated keys start at 1 RPS.  Public unauthenticated traffic can
        # be further throttled, so never hammer it with automatic retries.
        self.max_retries = 2 if self.api_key else 1


    def available(self) -> bool:
        # A process-level cooldown prevents new router instances from repeatedly
        # selecting the public shared pool while it is throttled.
        return time.monotonic() >= self.__class__._cooldown_until

    async def _pace(self) -> None:
        now = time.monotonic()
        if now < self.__class__._cooldown_until:
            remaining = self.__class__._cooldown_until - now
            raise ProviderUnavailable(
                self.source_id,
                f"Semantic Scholar cooldown active after HTTP 429 ({remaining:.0f}s remaining)",
                retry_after=remaining,
            )

        if not self.api_key:
            return

        async with self.__class__._pace_lock:
            remaining = 1.05 - (time.monotonic() - self.__class__._last_call)
            if remaining > 0:
                await asyncio.sleep(remaining)
            self.__class__._last_call = time.monotonic()

    @classmethod
    def _arm_cooldown(cls, seconds: float) -> None:
        cls._cooldown_until = max(cls._cooldown_until, time.monotonic() + max(1.0, seconds))

    async def search(self, query: str, limit: int = 5) -> List[EvidenceItem]:
        limit = max(1, min(20, int(limit)))
        cache_key = f"semanticscholar:{query}:{limit}"
        cached = cache_load(cache_key, 12 * 3600)
        if cached is not None:
            return [EvidenceItem(**row) for row in cached]

        await self._pace()

        fields = "title,abstract,year,authors,url,externalIds,openAccessPdf,citationCount,publicationTypes"
        headers = {}
        if self.api_key:
            headers["x-api-key"] = self.api_key

        try:
            r = await self._get(
                self.endpoint,
                params={"query": query, "limit": limit, "fields": fields},
                headers=headers,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code == 429:
                retry_after = self._retry_after(exc.response)
                # Public traffic is a shared pool.  If the provider supplies no
                # Retry-After, stay quiet for a minute and let Crossref/arXiv/
                # PLOS carry the query instead of immediately retrying.
                cooldown = retry_after if retry_after is not None else 60.0
                self._arm_cooldown(cooldown)
                logger.warning(
                    "semantic_scholar HTTP 429; cooling down for %.0fs%s",
                    cooldown,
                    " (public shared throttle)" if not self.api_key else "",
                )
            if exc.response is not None and exc.response.status_code == 429:
                raise ProviderUnavailable(
                    self.source_id,
                    "Semantic Scholar public/API quota is temporarily throttled",
                    retry_after=cooldown,
                ) from exc
            raise

        rows = r.json().get("data") or []
        out: List[EvidenceItem] = []
        total = max(1, len(rows))
        for idx, row in enumerate(rows):
            external = row.get("externalIds") or {}
            doi = external.get("DOI")
            paper_id = row.get("paperId")
            oa = row.get("openAccessPdf") or {}
            url = oa.get("url") or row.get("url") or ""
            out.append(EvidenceItem(
                source_id=self.source_id,
                title=row.get("title") or "",
                url=url,
                snippet=row.get("abstract") or "",
                authors=[a.get("name", "") for a in row.get("authors") or [] if a.get("name")],
                year=row.get("year"),
                doi=doi,
                external_id=f"s2:{paper_id}" if paper_id else None,
                evidence_type="scholarly_index_record",
                peer_review_status="unknown",
                authority="scholarly_index",
                relevance=max(0.55, 1.0 - (idx / (total + 2.0)) * 0.45),
                metadata={
                    "citation_count": row.get("citationCount"),
                    "publication_types": row.get("publicationTypes"),
                    "open_access_pdf": oa,
                    "external_ids": external,
                },
            ))
        cache_save(cache_key, [x.to_dict() for x in out])
        return out
