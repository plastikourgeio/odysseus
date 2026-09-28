"""Odysseus STEM Source Registry and bilingual retrieval layer."""

__version__ = "0.8.4"

from .models import EvidenceItem, QueryPlan, SourceSpec
from .registry import SourceRegistry
from .router import STEMSourceRouter
from .evidence import (
    canonicalize_url,
    contextual_relevance,
    deduplicate_evidence,
    evidence_scope,
    rank_evidence,
    rerank_evidence_for_query,
    scholarly_result_set_is_sufficient,
)
from .language import detect_language, preferred_response_language
from .policy import RetrievalDecision, decide_retrieval
from .official import (
    OfficialRoute,
    build_official_search_query,
    describe_routes,
    resolve_official_routes,
    url_matches_official_routes,
)
from .agent_bridge import wrap_web_search_handler
from .deep_research_bridge import install_deep_research_bridge

__all__ = [
    "EvidenceItem",
    "QueryPlan",
    "SourceSpec",
    "SourceRegistry",
    "STEMSourceRouter",
    "canonicalize_url",
    "deduplicate_evidence",
    "rank_evidence",
    "rerank_evidence_for_query",
    "contextual_relevance",
    "evidence_scope",
    "scholarly_result_set_is_sufficient",
    "RetrievalDecision",
    "decide_retrieval",
    "detect_language",
    "preferred_response_language",
    "OfficialRoute",
    "resolve_official_routes",
    "build_official_search_query",
    "url_matches_official_routes",
    "describe_routes",
    "wrap_web_search_handler",
    "install_deep_research_bridge",
]
