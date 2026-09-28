from __future__ import annotations

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

import httpx

from ..models import EvidenceItem

logger = logging.getLogger(__name__)


class ProviderUnavailable(RuntimeError):
    """A connector is temporarily unavailable but the retrieval pipeline may continue."""

    def __init__(self, source_id: str, reason: str, *, retry_after: float | None = None):
        super().__init__(reason)
        self.source_id = source_id
        self.retry_after = retry_after


class BaseConnector(ABC):
    source_id: str = "unknown"
    timeout_seconds: float = 20.0
    max_retries: int = 2

    def available(self) -> bool:
        return True

    def headers(self) -> Dict[str, str]:
        contact = os.getenv("STEM_CONTACT_EMAIL", "").strip()
        agent = "OdysseusSTEM/0.8"
        if contact:
            agent += f" (mailto:{contact})"
        return {"User-Agent": agent, "Accept": "application/json, application/xml, text/xml;q=0.9, */*;q=0.1"}

    async def _get(self, url: str, *, params: Optional[Dict[str, Any]] = None,
                   headers: Optional[Dict[str, str]] = None) -> httpx.Response:
        """GET with retries only for genuinely transient failures.

        v0.4 retried every HTTP error through the generic exception handler.
        That is counterproductive for deterministic 4xx responses such as
        arXiv 406.  v0.4 retries transport failures and 429/502/503/504 only.
        """
        merged = self.headers()
        if headers:
            merged.update(headers)
        last_error: Optional[Exception] = None
        transient_statuses = {429, 502, 503, 504}

        async with httpx.AsyncClient(follow_redirects=True) as client:
            for attempt in range(1, self.max_retries + 1):
                try:
                    r = await client.get(url, params=params, headers=merged, timeout=self.timeout_seconds)
                    if r.status_code in transient_statuses:
                        if attempt < self.max_retries:
                            delay = self._retry_after(r) or min(8.0, 2.0 ** attempt)
                            logger.warning(
                                "%s transient HTTP %s; retrying in %.1fs",
                                self.source_id, r.status_code, delay,
                            )
                            await asyncio.sleep(delay)
                            continue
                        r.raise_for_status()
                    # Non-transient HTTP failures fail fast.  Callers that know
                    # a provider-specific compatibility fallback can handle it.
                    r.raise_for_status()
                    return r
                except httpx.HTTPStatusError:
                    raise
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    last_error = exc
                    if attempt >= self.max_retries:
                        raise
                    await asyncio.sleep(min(8.0, 2.0 ** attempt))
                except Exception as exc:
                    last_error = exc
                    raise
        assert last_error is not None
        raise last_error

    @staticmethod
    def _retry_after(r: httpx.Response) -> Optional[float]:
        raw = r.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return max(0.0, float(raw))
        except ValueError:
            try:
                dt = parsedate_to_datetime(raw)
                return max(0.0, dt.timestamp() - __import__("time").time())
            except Exception:
                return None

    @abstractmethod
    async def search(self, query: str, limit: int = 5) -> List[EvidenceItem]:
        raise NotImplementedError
