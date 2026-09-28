from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, Iterable, Optional

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
_MIN_SCHOLARLY_RESULTS = 2
_MAX_SCHOLARLY_RESULTS = 8


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


def _extract_query(content: str) -> tuple[str, Optional[dict]]:
    """Parse a web_search payload without changing its public schema.

    Odysseus native function calls normally convert ``web_search({query: ...})``
    back into a *plain query string* before execution. Direct/manual calls often
    arrive as JSON. The bridge must support both forms or it only works in tests
    and direct TOOL_HANDLERS calls while silently bypassing real Agent traffic.
    """
    raw = str(content or "").strip()
    if not raw:
        return "", None

    # Plain text is the normal Agent/native-function execution shape.
    if not raw.startswith("{"):
        query = raw.split("\n", 1)[0].strip()
        return query, {"query": query}

    try:
        from src.tool_utils import _parse_tool_args
        args = _parse_tool_args(raw)
    except Exception:
        try:
            args = json.loads(raw)
        except Exception:
            args = {}

    if not isinstance(args, dict):
        return "", None

    # Keep multi-query calls on the existing web-search path. They may encode
    # deliberate provider behavior that this integration should not alter.
    if args.get("queries"):
        return "", args

    query = str(args.get("query") or "").strip()
    return query, args


def _replace_query(args: dict, query: str) -> str:
    payload = dict(args or {})
    payload["query"] = query
    payload.pop("queries", None)
    return json.dumps(payload, ensure_ascii=False)


def _source_payload(item: EvidenceItem) -> dict:
    scores = dict(item.metadata.get("ranking_scores") or {})
    return {
        "url": _https_url(item.url),
        "title": item.title,
        "snippet": (item.snippet or "")[:700],
        "source_id": item.source_id,
        "evidence_type": item.evidence_type,
        "evidence_scope": evidence_scope(item),
        "peer_review_status": item.peer_review_status,
        "semantic_relevance": round(float(item.relevance or 0.0), 4),
        "final_score": scores.get("final_score"),
        "context_fit": item.metadata.get("context_fit", ""),
    }


def _format_scholarly_output(query: str, items: Iterable[EvidenceItem]) -> str:
    rows = list(items)[:_MAX_SCHOLARLY_RESULTS]
    lines = [
        f"STEM scholarly evidence for: {query}",
        "",
        "Semantic relevance, source authority and final ranking are separate. A preprint is not treated as peer reviewed merely because it is indexed. Bibliographic-only records identify publications but do not by themselves support technical claims.",
        "Retrieval status: sufficient structured scholarly evidence. Do not repeat general web_search unless broader web coverage is explicitly needed or a required claim remains unsupported.",
        "",
    ]

    sources = []
    for i, item in enumerate(rows, 1):
        source = _source_payload(item)
        sources.append(source)
        fit = source.get("context_fit") or "unknown"
        peer = source.get("peer_review_status") or "unknown"
        scope = source.get("evidence_scope") or "unknown"
        year = item.year or "n.d."
        final_score = source.get("final_score")
        final_text = f"{float(final_score):.3f}" if isinstance(final_score, (int, float)) else "n/a"
        lines.extend([
            f"[{i}] {item.title}",
            (
                f"Source: {item.source_id} | year: {year} | type: {item.evidence_type} | "
                f"scope: {scope} | peer review: {peer} | fit: {fit} | "
                f"semantic: {float(item.relevance or 0.0):.3f} | rank: {final_text}"
            ),
            f"URL: {source['url']}",
        ])
        if item.doi:
            lines.append(f"DOI: {item.doi}")
        if item.snippet:
            if scope == "bibliographic_only":
                lines.append(f"Bibliographic metadata: {(item.snippet or '').strip()[:900]}")
                lines.append("Use: discovery/publication metadata only; fetch the paper or a content-bearing source before using it to support a technical claim.")
            else:
                lines.append(f"Abstract/summary: {(item.snippet or '').strip()[:900]}")
        lines.append("")

    # Existing agent_loop extracts this marker, emits source cards and strips it
    # from the LLM-visible text.
    lines.append("<!-- SOURCES:" + json.dumps(sources, ensure_ascii=False) + " -->")
    return "\n".join(lines)


def _usable_scholarly(items: Iterable[EvidenceItem]) -> list[EvidenceItem]:
    return [
        item for item in items
        if float(item.relevance or 0.0) >= 0.35
        and item.metadata.get("context_fit", "related") in {"direct", "related"}
    ][:_MAX_SCHOLARLY_RESULTS]


def _extract_sources(result: dict) -> list[dict]:
    text = str(result.get("output") or result.get("results") or result.get("stdout") or "")
    marker = "<!-- SOURCES:"
    start = text.find(marker)
    if start < 0:
        return []
    end = text.find(" -->", start)
    if end < 0:
        return []
    try:
        rows = json.loads(text[start + len(marker):end])
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def _prefix_output(result: dict, prefix: str) -> dict:
    out = dict(result)
    for key in ("output", "results", "stdout"):
        if key in out and isinstance(out[key], str):
            out[key] = prefix.rstrip() + "\n\n" + out[key]
            break
    return out


def wrap_web_search_handler(
    original_handler: Callable[..., Any],
    *,
    router_factory: Callable[[], STEMSourceRouter] = STEMSourceRouter,
):
    """Add evidence-aware STEM routing to the existing ``web_search`` tool.

    - scholarly queries try structured STEM scholarly connectors first;
    - known named-product technical queries search official documentation first;
    - shopping/live-stock queries and unknown products remain on the existing
      Brave/Tavily path;
    - every failed/insufficient specialized route falls back to the original
      handler unchanged.
    """
    current_version = getattr(original_handler, "_stem_bridge_version", None)
    if current_version == _BRIDGE_VERSION:
        return original_handler
    if current_version:
        original_handler = getattr(original_handler, "_stem_original_handler", original_handler)

    async def wrapped(content: str, ctx: Dict[str, Any]):
        if not _setting_enabled("stem_source_bridge_enabled", True):
            return await original_handler(content, ctx)

        query, args = _extract_query(content)
        if not query or not args:
            return await original_handler(content, ctx)

        try:
            router = router_factory()
            plan = router.plan_query(query)
            policy = router.retrieval_policy(
                query,
                intent=plan.intent,
                domains=plan.domains,
            )

            if policy.scholarly:
                scholarly_query = normalize_scholarly_query(query) or query
                if scholarly_query != query:
                    logger.info(
                        "[stem-search] scholarly normalized query=%r -> %r",
                        query[:240],
                        scholarly_query[:240],
                    )
                evidence = await router.retrieve_scholarly(
                    scholarly_query,
                    limit_per_source=4,
                    max_connectors=4,
                    intent_override="research",
                )
                usable = _usable_scholarly(evidence)
                if scholarly_result_set_is_sufficient(
                    usable,
                    scholarly_query,
                    min_results=_MIN_SCHOLARLY_RESULTS,
                ):
                    logger.info(
                        "[stem-search] route=scholarly intent=%s domains=%s results=%d query=%r",
                        plan.intent,
                        list(plan.domains),
                        len(usable),
                        query[:240],
                    )
                    return {
                        "output": _format_scholarly_output(query, usable),
                        "exit_code": 0,
                    }
                logger.info(
                    "[stem-search] scholarly route insufficient (%d); considering official/web fallback",
                    len(usable),
                )

            # Live retail/availability should not be trapped inside manufacturer
            # docs. Explicit site queries already express the desired routing.
            explicit_site = "site:" in query.casefold()
            if policy.official and _setting_enabled("stem_official_docs_bridge_enabled", True) and plan.intent != "purchase" and not explicit_site:
                routes = resolve_official_routes(query)
                if routes:
                    base_query = plan.official_queries[-1] if plan.official_queries else query
                    official_query = build_official_search_query(base_query, routes)
                    official_result = await original_handler(_replace_query(args, official_query), ctx)
                    official_sources = _extract_sources(official_result)
                    accepted = [
                        row for row in official_sources
                        if isinstance(row, dict)
                        and url_matches_official_routes(str(row.get("url") or ""), routes)
                    ]
                    if accepted:
                        labels = describe_routes(routes)
                        logger.info(
                            "[stem-search] route=official sources=%s accepted=%d query=%r",
                            labels,
                            len(accepted),
                            query[:240],
                        )
                        return _prefix_output(
                            official_result,
                            (
                                f"STEM official documentation route: {labels}. "
                                "Use these primary sources for product specifications, compatibility, firmware, APIs and settings; "
                                "do not infer undocumented limits. Primary-source discovery succeeded: prefer web_fetch on the listed official sources "
                                "instead of repeating web_search unless a required field is still missing."
                            ),
                        )
                    logger.info(
                        "[stem-search] official route returned no accepted official source; falling back to original web_search query=%r",
                        query[:240],
                    )

            return await original_handler(content, ctx)
        except Exception as exc:
            logger.warning(
                "[stem-search] bridge failed; falling back to original web_search: %s",
                exc,
            )
            return await original_handler(content, ctx)

    wrapped._stem_bridge_version = _BRIDGE_VERSION
    wrapped._stem_original_handler = original_handler
    return wrapped
