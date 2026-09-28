from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class SourceSpec:
    id: str
    name: str
    kind: str
    domains: Tuple[str, ...]
    intents: Tuple[str, ...]
    authority: str
    connector: Optional[str] = None
    peer_review: Optional[bool] = None
    requires_crosscheck: bool = False
    cost: str = "free"
    notes: str = ""
    labels: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class QueryPlan:
    original_query: str
    detected_language: str
    response_language: str
    intent: str
    domains: Tuple[str, ...]
    official_queries: Tuple[str, ...]
    scholarly_queries: Tuple[str, ...]
    general_queries: Tuple[str, ...]
    technical_terms: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "original_query": self.original_query,
            "detected_language": self.detected_language,
            "response_language": self.response_language,
            "intent": self.intent,
            "domains": list(self.domains),
            "official_queries": list(self.official_queries),
            "scholarly_queries": list(self.scholarly_queries),
            "general_queries": list(self.general_queries),
            "technical_terms": list(self.technical_terms),
        }


@dataclass
class EvidenceItem:
    source_id: str
    title: str
    url: str
    snippet: str = ""
    authors: List[str] = field(default_factory=list)
    year: Optional[int] = None
    doi: Optional[str] = None
    external_id: Optional[str] = None
    evidence_type: str = "unknown"
    peer_review_status: str = "unknown"
    authority: str = "unknown"
    relevance: float = 0.5
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "authors": list(self.authors),
            "year": self.year,
            "doi": self.doi,
            "external_id": self.external_id,
            "evidence_type": self.evidence_type,
            "peer_review_status": self.peer_review_status,
            "authority": self.authority,
            "relevance": self.relevance,
            "metadata": dict(self.metadata),
        }
