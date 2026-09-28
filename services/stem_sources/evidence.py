from __future__ import annotations

import re
from typing import Iterable, List
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import EvidenceItem

_TRACKING_KEYS = {
    "gclid", "fbclid", "mc_cid", "mc_eid", "queryid", "ref", "source",
}
_AUTHORITY_WEIGHT = {
    "primary": 1.00,
    "institutional": 0.92,
    "peer_reviewed": 0.90,
    "scholarly_index": 0.78,
    "preprint": 0.68,
    "community": 0.48,
    "unknown": 0.50,
}


def normalize_doi(value: str | None) -> str:
    if not value:
        return ""
    doi = value.strip().lower()
    doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi)
    doi = re.sub(r"^doi:\s*", "", doi)
    return doi.rstrip(" .")


def normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).strip()


def canonicalize_url(value: str) -> str:
    if not value:
        return ""
    try:
        p = urlsplit(value.strip())
        host = p.hostname.lower() if p.hostname else ""
        port = p.port
        netloc = host
        if port and not ((p.scheme == "http" and port == 80) or (p.scheme == "https" and port == 443)):
            netloc = f"{host}:{port}"
        pairs = []
        for k, v in parse_qsl(p.query, keep_blank_values=True):
            kl = k.casefold()
            if kl.startswith("utm_") or kl in _TRACKING_KEYS:
                continue
            pairs.append((k, v))
        query = urlencode(sorted(pairs))
        path = p.path or "/"
        if path != "/":
            path = path.rstrip("/")
        return urlunsplit((p.scheme.lower(), netloc, path, query, ""))
    except Exception:
        return value.strip()


def _key(item: EvidenceItem) -> str:
    doi = normalize_doi(item.doi)
    if doi:
        return f"doi:{doi}"
    if item.external_id:
        return f"ext:{item.external_id.casefold()}"
    title = normalize_title(item.title)
    if title:
        return f"title:{title}"
    return f"url:{canonicalize_url(item.url)}"


def deduplicate_evidence(items: Iterable[EvidenceItem]) -> List[EvidenceItem]:
    merged: dict[str, EvidenceItem] = {}
    for item in items:
        key = _key(item)
        prev = merged.get(key)
        if prev is None:
            item.url = canonicalize_url(item.url)
            item.doi = normalize_doi(item.doi) or None
            merged[key] = item
            continue
        # Prefer the record with higher semantic relevance, while preserving
        # richer fields from the alternate index/host.
        winner, other = (item, prev) if item.relevance > prev.relevance else (prev, item)
        winner.metadata = {**other.metadata, **winner.metadata}
        if not winner.snippet and other.snippet:
            winner.snippet = other.snippet
        if not winner.authors and other.authors:
            winner.authors = other.authors
        if not winner.year and other.year:
            winner.year = other.year
        if not winner.doi and other.doi:
            winner.doi = normalize_doi(other.doi) or None
        winner.url = canonicalize_url(winner.url or other.url)
        merged[key] = winner
    return list(merged.values())


def evidence_scope(item: EvidenceItem) -> str:
    """Describe what the retrieved record can safely support.

    Crossref-style records are discovery/bibliographic evidence, not technical
    content evidence. Other records with a real abstract/summary may support
    synthesis subject to their review status.
    """
    explicit = str(item.metadata.get("evidence_scope") or "").strip()
    if explicit:
        return explicit
    if item.evidence_type == "bibliographic_metadata":
        return "bibliographic_only"
    if item.snippet:
        return "abstract_or_summary"
    return "discovery_only"


def _final_ranking_score(item: EvidenceItem) -> float:
    semantic = max(0.0, min(1.0, float(item.relevance or 0.0)))
    authority = _AUTHORITY_WEIGHT.get(item.authority, _AUTHORITY_WEIGHT["unknown"])
    peer_bonus = 0.05 if item.peer_review_status == "peer_reviewed" else 0.0
    scope = evidence_scope(item)
    # A bibliographic index can confirm that a work exists and expose metadata,
    # but it should not outrank content-bearing evidence merely because the
    # index itself is authoritative.
    scope_penalty = 0.08 if scope == "bibliographic_only" else 0.0
    score = (semantic * 0.72) + (authority * 0.28) + peer_bonus - scope_penalty
    score = max(0.0, min(1.0, score))

    item.metadata = dict(item.metadata)
    item.metadata["evidence_scope"] = scope
    item.metadata["ranking_scores"] = {
        "semantic_relevance": round(semantic, 4),
        "authority_weight": round(authority, 4),
        "peer_review_bonus": round(peer_bonus, 4),
        "scope_penalty": round(scope_penalty, 4),
        "final_score": round(score, 4),
    }
    return score


def rank_evidence(items: Iterable[EvidenceItem]) -> List[EvidenceItem]:
    rows = list(items)
    scored = [(_final_ranking_score(item), index, item) for index, item in enumerate(rows)]
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [item for _, _, item in scored]


_QUERY_STOPWORDS = {
    "research", "study", "studies", "investigation", "investigacion", "paper", "papers",
    "proceeding", "proceedings", "journal", "journals", "article", "articles",
    "publication", "publications", "doi", "pdf", "site", "filetype", "inurl", "intitle",
    "about", "sobre", "into", "the", "and", "or", "not", "de", "la", "el", "en", "for", "with",
    "robotics", "systems", "system", "science", "engineering",
}

_CONCEPT_VARIANTS = {
    "education": {"education", "educational", "teaching", "classroom", "student", "students", "school", "pedagogy", "stem education", "learning environment", "learning activity"},
    "obstacle avoidance": {"obstacle avoidance", "obstacle detection and avoidance", "collision avoidance", "avoid obstacles", "obstacle detection"},
    "robot": {"robot", "robots", "robotic", "robotics", "mobile robot", "autonomous robot"},
    "3d printing": {"3d printing", "additive manufacturing", "fused filament", "fff", "fdm"},
    "machine learning": {"machine learning", "ml", "deep learning", "neural network", "neural networks"},
    "computer vision": {"computer vision", "vision-based", "image recognition", "image processing"},
    "embedded systems": {"embedded system", "embedded systems", "microcontroller", "microcontrollers"},
}


def _fold(value: str) -> str:
    import unicodedata
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return value.casefold()


def _query_concepts(query: str, technical_terms=()) -> List[tuple[str, set[str]]]:
    q = _fold(query)
    concepts: List[tuple[str, set[str]]] = []
    seen = set()

    def add(name: str, variants) -> None:
        key = _fold(name).strip()
        if not key or key in seen:
            return
        seen.add(key)
        concepts.append((key, {_fold(v) for v in variants if v}))

    for term in technical_terms or ():
        key = _fold(term).strip()
        variants = _CONCEPT_VARIANTS.get(key, {key})
        add(key, variants)

    # Preserve important contextual concepts even if the bilingual glossary did
    # not explicitly emit them as technical terms.
    for key, variants in _CONCEPT_VARIANTS.items():
        if any(_variant_in_text(v, q) for v in variants):
            add(key, variants)

    for tok in re.findall(r"[a-z0-9+#.-]+", q):
        if len(tok) < 3 or tok in _QUERY_STOPWORDS:
            continue
        if any(
            tok == name
            or _variant_in_text(tok, name)
            or any(_variant_in_text(tok, variant) for variant in variants)
            for name, variants in concepts
        ):
            continue
        add(tok, {tok})
        if len(concepts) >= 8:
            break

    return concepts


def _variant_in_text(variant: str, text: str) -> bool:
    variant = _fold(variant).strip()
    if not variant:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(variant).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
    return bool(re.search(pattern, text))


def contextual_relevance(item: EvidenceItem, query: str, technical_terms=()) -> float:
    """Re-score provider results against the actual user research context.

    ``item.relevance`` becomes semantic relevance only. Authority and evidence
    scope are deliberately kept separate and combined later by ``rank_evidence``.
    """
    concepts = _query_concepts(query, technical_terms)
    if not concepts:
        score = max(0.0, min(1.0, float(item.relevance or 0.0)))
        item.metadata = dict(item.metadata)
        item.metadata["semantic_relevance"] = round(score, 4)
        return score

    title = _fold(item.title)
    body = _fold(f"{item.title} {item.snippet}")
    matched = 0
    title_hits = 0
    details = {}

    for name, variants in concepts:
        in_title = any(_variant_in_text(v, title) for v in variants)
        in_body = in_title or any(_variant_in_text(v, body) for v in variants)
        details[name] = bool(in_body)
        if in_body:
            matched += 1
        if in_title:
            title_hits += 1

    coverage = matched / max(1, len(concepts))
    title_coverage = title_hits / max(1, len(concepts))
    provider_prior = max(0.0, min(1.0, float(item.relevance or 0.0)))

    score = (coverage * 0.58) + (title_coverage * 0.22) + (provider_prior * 0.20)

    critical = []
    if "education" in dict(concepts):
        critical.append("education")
    if "obstacle avoidance" in dict(concepts):
        critical.append("obstacle avoidance")

    critical_hits = sum(1 for key in critical if details.get(key))
    if critical:
        if critical_hits == len(critical):
            score += 0.12
        else:
            score -= 0.18 * (len(critical) - critical_hits)

    score = max(0.0, min(1.0, score))
    item.metadata = dict(item.metadata)
    item.metadata["semantic_relevance"] = round(score, 4)
    item.metadata["contextual_ranking"] = {
        "coverage": round(coverage, 4),
        "title_coverage": round(title_coverage, 4),
        "matched_concepts": [k for k, hit in details.items() if hit],
        "missing_concepts": [k for k, hit in details.items() if not hit],
        "provider_prior": round(provider_prior, 4),
    }
    item.metadata["context_fit"] = (
        "direct" if coverage >= 0.75 and (not critical or critical_hits == len(critical))
        else "related" if coverage >= 0.45
        else "background"
    )
    return score


def rerank_evidence_for_query(items: Iterable[EvidenceItem], query: str, technical_terms=()) -> List[EvidenceItem]:
    rescored = list(items)
    for item in rescored:
        item.relevance = contextual_relevance(item, query, technical_terms)
    return rank_evidence(rescored)


def scholarly_result_set_is_sufficient(
    items: Iterable[EvidenceItem],
    query: str,
    *,
    min_results: int = 2,
) -> bool:
    """Return whether a structured scholarly result set can stop web fallback.

    A result set must contain enough rows and at least one content-bearing
    record.  For the educational-obstacle-avoidance case that exposed the
    v0.8.3 false positive, require a *direct* content-bearing match: several
    generic robotics papers that merely mention obstacle avoidance are not
    sufficient evidence for an explicit educational-robotics request.

    The stricter direct-match rule is intentionally narrow and based on the
    same concepts used by the contextual ranker, rather than making every
    multi-term research query require an exact title match.
    """
    rows = list(items)
    if len(rows) < max(1, int(min_results)):
        return False

    content_bearing = [
        item for item in rows
        if evidence_scope(item) not in {"bibliographic_only", "discovery_only"}
    ]
    if not content_bearing:
        return False

    concept_names = {name for name, _ in _query_concepts(query)}
    requires_direct_context = {
        "education",
        "obstacle avoidance",
    }.issubset(concept_names)

    if requires_direct_context:
        return any(
            item.metadata.get("context_fit") == "direct"
            and float(item.relevance or 0.0) >= 0.60
            for item in content_bearing
        )

    return True
