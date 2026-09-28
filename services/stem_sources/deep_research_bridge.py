from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List

from .evidence import evidence_scope, scholarly_result_set_is_sufficient
from .models import EvidenceItem
from .official import (
    build_official_search_query,
    describe_routes,
    resolve_official_routes,
    url_matches_official_routes,
)
from .router import STEMSourceRouter
from .query_cleanup import normalize_scholarly_query

logger = logging.getLogger(__name__)

_BRIDGE_VERSION = "0.8.4"


def _setting_enabled(name: str, default: bool = True) -> bool:
    try:
        from src.settings import get_setting
        value = get_setting(name, default)
    except Exception:
        return default
    if isinstance(value, str):
        return value.strip().casefold() not in {"0", "false", "no", "off", "disabled"}
    return bool(value)


def _https_url(url: str) -> str:
    value = (url or "").strip()
    if value.startswith("http://arxiv.org/"):
        return "https://arxiv.org/" + value[len("http://arxiv.org/"):]
    return value


def _as_search_result(item: EvidenceItem) -> Dict[str, Any]:
    scores = dict(item.metadata.get("ranking_scores") or {})
    scope = evidence_scope(item)
    snippet = (item.snippet or "")[:1200]
    if scope == "bibliographic_only" and snippet:
        snippet = "Bibliographic metadata only: " + snippet
    return {
        "url": _https_url(item.url),
        "title": item.title,
        "snippet": snippet,
        "source_id": item.source_id,
        "evidence_type": item.evidence_type,
        "evidence_scope": scope,
        "peer_review_status": item.peer_review_status,
        "semantic_relevance": float(item.relevance or 0.0),
        "final_score": scores.get("final_score"),
        "context_fit": item.metadata.get("context_fit", ""),
    }


def install_deep_research_bridge(
    researcher_cls,
    *,
    router_factory: Callable[[], STEMSourceRouter] = STEMSourceRouter,
):
    """Wrap ``DeepResearcher._search`` with scholarly + official routing.

    The existing provider chain remains authoritative for generic web/current
    queries and is always the fallback if a specialized route is insufficient.
    """
    current = getattr(researcher_cls, "_search", None)
    if current is None:
        raise AttributeError("DeepResearcher has no _search method")
    current_version = getattr(current, "_stem_bridge_version", None)
    if current_version == _BRIDGE_VERSION:
        return researcher_cls
    original = getattr(current, "_stem_original_search", current) if current_version else current

    async def wrapped(self, query: str) -> List[Dict]:
        if not _setting_enabled("stem_deep_research_bridge_enabled", True):
            return await original(self, query)

        try:
            router = router_factory()
            plan = router.plan_query(query)
            policy = router.retrieval_policy(
                query,
                intent=plan.intent,
                domains=plan.domains,
            )
            explicit_site = "site:" in (query or "").casefold()

            if policy.scholarly and not explicit_site:
                scholarly_query = normalize_scholarly_query(query) or query
                evidence = await router.retrieve_scholarly(
                    scholarly_query,
                    limit_per_source=4,
                    max_connectors=4,
                    intent_override="research",
                )
                usable = [
                    item for item in evidence
                    if float(item.relevance or 0.0) >= 0.35
                    and item.metadata.get("context_fit", "related") in {"direct", "related"}
                ][:10]
                if scholarly_result_set_is_sufficient(
                    usable,
                    scholarly_query,
                    min_results=2,
                ):
                    providers_used = getattr(self, "providers_used", None)
                    if isinstance(providers_used, list) and "stem_scholarly" not in providers_used:
                        providers_used.append("stem_scholarly")
                    logger.info(
                        "Research search: stem_scholarly returned %d results for %r",
                        len(usable),
                        (query or "")[:220],
                    )
                    return [_as_search_result(item) for item in usable]
                logger.info(
                    "Research search: stem_scholarly insufficient (%d); considering official/web provider chain",
                    len(usable),
                )

            if policy.official and _setting_enabled("stem_official_docs_bridge_enabled", True) and plan.intent != "purchase" and not explicit_site:
                routes = resolve_official_routes(query)
                if routes:
                    base_query = plan.official_queries[-1] if plan.official_queries else query
                    official_query = build_official_search_query(base_query, routes)
                    rows = await original(self, official_query)
                    accepted = [
                        row for row in rows
                        if isinstance(row, dict)
                        and url_matches_official_routes(str(row.get("url") or ""), routes)
                    ]
                    if accepted:
                        providers_used = getattr(self, "providers_used", None)
                        if isinstance(providers_used, list) and "stem_official" not in providers_used:
                            providers_used.append("stem_official")
                        logger.info(
                            "Research search: stem_official (%s) returned %d accepted results for %r",
                            describe_routes(routes),
                            len(accepted),
                            (query or "")[:220],
                        )
                        return accepted
                    logger.info(
                        "Research search: stem_official returned no accepted result; using original query/provider chain"
                    )
        except Exception as exc:
            logger.warning(
                "Research search: STEM bridge failed; using configured web provider chain: %s",
                exc,
            )

        return await original(self, query)

    wrapped._stem_bridge_version = _BRIDGE_VERSION
    wrapped._stem_original_search = original
    researcher_cls._search = wrapped
    return researcher_cls
