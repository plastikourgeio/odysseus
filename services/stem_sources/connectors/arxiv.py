from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import List

from .base import BaseConnector, ProviderUnavailable
from ..cache import load as cache_load, save as cache_save
from ..models import EvidenceItem

logger = logging.getLogger(__name__)


class ArxivConnector(BaseConnector):
    """arXiv connector backed by the maintained ``arxiv.py`` client.

    Odysseus previously talked to export.arxiv.org directly with httpx.  On the
    Raspberry Pi route that repeatedly produced HTTP 406 even with permissive
    Accept headers and the documented HTTP/HTTPS endpoints.  v0.5.2 stops guessing
    at legacy endpoint headers and delegates protocol details, retries, Atom
    parsing and the client user-agent behavior to the maintained arxiv.py
    project.

    The third-party package runs in a short-lived subprocess with an isolated
    PYTHONPATH rooted in /app/data/stem_arxiv_vendor.  This keeps its dependency
    versions out of the main Odysseus process while preserving them across
    container recreation through /app/data.
    """

    source_id = "arxiv"
    _lock = asyncio.Lock()
    _last_call = 0.0
    worker_timeout_seconds = 60.0

    @staticmethod
    def _vendor_dir() -> Path:
        return Path(os.getenv("STEM_ARXIV_VENDOR", "/app/data/stem_arxiv_vendor"))

    @classmethod
    def available(cls) -> bool:
        vendor = cls._vendor_dir()
        return vendor.exists() and any(vendor.glob("arxiv*"))

    async def _respect_rate_limit(self) -> None:
        # Keep separate worker invocations polite too. arxiv.py itself handles
        # delays/retries inside a worker, but a new worker has no prior call time.
        async with self._lock:
            remaining = 3.1 - (time.monotonic() - self.__class__._last_call)
            if remaining > 0:
                await asyncio.sleep(remaining)
            self.__class__._last_call = time.monotonic()

    @staticmethod
    def _api_search_query(query: str) -> str:
        """Build a concise arXiv query without over-constraining the corpus."""
        text = " ".join((query or "").split()).strip()
        if not text:
            return "all:*"

        lowered = text.casefold()
        parts: List[str] = []
        consumed = text

        phrase_map = [
            "obstacle avoidance",
            "machine learning",
            "artificial intelligence",
            "computer vision",
            "embedded systems",
            "materials science",
            "3d printing",
        ]
        for phrase in phrase_map:
            if phrase in lowered:
                parts.append(f'all:"{phrase}"')
                consumed = re.sub(re.escape(phrase), " ", consumed, flags=re.I)

        # Exact `all:"educational robotics"` is needlessly brittle.  Express
        # it as the two concepts so results phrased as "robotics education",
        # "educational robot", etc. are still reachable.
        educational_robotics = bool(
            re.search(r"\beducational\s+robotics\b|\brobotics\s+education\b", lowered)
        )
        if educational_robotics:
            consumed = re.sub(r"\beducational\s+robotics\b|\brobotics\s+education\b", " ", consumed, flags=re.I)

        tokens = [
            t.casefold()
            for t in re.findall(r"[A-Za-z0-9_+.-]+", consumed)
            if len(t) > 1
        ]

        # Generic research words add little retrieval value and can make an AND
        # query unnecessarily restrictive.
        stop = {
            "research", "study", "studies", "investigation", "investigacion",
            "about", "sobre", "into", "en", "the", "and", "or", "not", "de", "la", "el",
            "paper", "papers", "proceeding", "proceedings", "journal", "journals",
            "article", "articles", "publication", "publications", "doi",
            "site", "filetype", "inurl", "intitle", "pdf", "arxiv.org",
        }
        tokens = [t for t in tokens if t not in stop]

        # Collapse common variants and keep the educational context broad enough
        # to catch "educational robotics", "robotics education" and STEM teaching.
        has_robot = educational_robotics or any(t in {"robot", "robots", "robotic", "robotics"} for t in tokens)
        has_education = educational_robotics or any(t in {"education", "educational", "teaching", "learning"} for t in tokens)
        if has_robot:
            parts.append("(all:robot OR all:robotics OR all:robotic)")
            tokens = [t for t in tokens if t not in {"robot", "robots", "robotic", "robotics"}]
        if has_education:
            parts.append("(all:education OR all:educational OR all:teaching OR all:learning OR all:STEM)")
            tokens = [t for t in tokens if t not in {"education", "educational", "teaching", "learning", "stem"}]

        seen = set()
        for token in tokens:
            if token in seen:
                continue
            seen.add(token)
            parts.append(f"all:{token}")
            if len(parts) >= 8:
                break

        return " AND ".join(parts) if parts else f'all:"{text}"'

    async def _run_worker(self, api_query: str, limit: int) -> List[dict]:
        worker = Path(__file__).with_name("arxiv_worker.py")
        vendor = self._vendor_dir()
        if not worker.exists():
            raise ProviderUnavailable(self.source_id, f"arxiv.py worker missing: {worker}")
        if not self.available():
            raise ProviderUnavailable(
                self.source_id,
                f"arxiv.py vendor package missing under {vendor}",
            )

        env = os.environ.copy()
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = str(vendor) + ((os.pathsep + existing) if existing else "")

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(worker),
            api_query,
            str(limit),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.worker_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.communicate()
            raise ProviderUnavailable(self.source_id, "arxiv.py worker timed out") from exc

        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0:
            tail = stderr_text[-1600:] if stderr_text else f"exit code {proc.returncode}"
            # A persistent provider-side 406 through the maintained client is a
            # route/provider incompatibility, not something to hammer with more
            # handcrafted retries.
            raise ProviderUnavailable(self.source_id, f"arxiv.py worker failed: {tail}")

        try:
            payload = json.loads(stdout.decode("utf-8"))
        except Exception as exc:
            raise ProviderUnavailable(
                self.source_id,
                f"arxiv.py worker returned invalid JSON; stderr={stderr_text[-500:]}",
            ) from exc
        return payload if isinstance(payload, list) else []

    async def search(self, query: str, limit: int = 5) -> List[EvidenceItem]:
        limit = max(1, min(20, int(limit)))
        cache_key = f"arxiv-v052:{query}:{limit}"
        cached = cache_load(cache_key, 12 * 3600)
        if cached is not None:
            return [EvidenceItem(**row) for row in cached]

        await self._respect_rate_limit()
        api_query = self._api_search_query(query)
        rows = await self._run_worker(api_query, limit)

        out: List[EvidenceItem] = []
        for idx, row in enumerate(rows):
            published = str(row.get("published") or "")
            year = int(published[:4]) if published[:4].isdigit() else None
            url = row.get("entry_id") or row.get("pdf_url") or ""
            external_id = str(row.get("arxiv_id") or "").strip()
            out.append(EvidenceItem(
                source_id=self.source_id,
                title=row.get("title") or "",
                url=url,
                snippet=row.get("summary") or "",
                authors=[str(a) for a in (row.get("authors") or []) if a],
                year=year,
                doi=row.get("doi") or None,
                external_id=f"arxiv:{external_id}" if external_id else None,
                evidence_type="scholarly_preprint",
                peer_review_status="preprint_not_verified",
                authority="preprint",
                relevance=max(0.55, 1.0 - (idx * 0.04)),
                metadata={
                    "warning": "Preprint: peer review status is not established by arXiv.",
                    "primary_category": row.get("primary_category"),
                    "categories": row.get("categories") or [],
                    "comment": row.get("comment"),
                    "journal_ref": row.get("journal_ref"),
                    "client": "arxiv.py",
                },
            ))

        cache_save(cache_key, [x.to_dict() for x in out])
        return out
