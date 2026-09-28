from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .models import SourceSpec


class SourceRegistry:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or Path(__file__).with_name("sources.json")
        self._sources = self._load()

    def _load(self) -> Dict[str, SourceSpec]:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        out: Dict[str, SourceSpec] = {}
        for row in raw.get("sources", []):
            spec = SourceSpec(
                id=row["id"],
                name=row["name"],
                kind=row["kind"],
                domains=tuple(row.get("domains", ["*"])),
                intents=tuple(row.get("intents", ["*"])),
                authority=row.get("authority", "unknown"),
                connector=row.get("connector"),
                peer_review=row.get("peer_review"),
                requires_crosscheck=bool(row.get("requires_crosscheck", False)),
                cost=row.get("cost", "free"),
                notes=row.get("notes", ""),
                labels=dict(row.get("labels", {})),
            )
            if spec.id in out:
                raise ValueError(f"Duplicate STEM source id: {spec.id}")
            out[spec.id] = spec
        return out

    def get(self, source_id: str) -> Optional[SourceSpec]:
        return self._sources.get(source_id)

    def all(self) -> List[SourceSpec]:
        return list(self._sources.values())

    def connected(self) -> List[SourceSpec]:
        return [s for s in self._sources.values() if s.connector]

    def match(self, domains: Iterable[str], intent: str) -> List[SourceSpec]:
        domain_set = {d for d in domains if d}
        scored = []
        for spec in self._sources.values():
            d = set(spec.domains)
            i = set(spec.intents)
            domain_score = 3 if "*" in d else 4 * len(domain_set & d)
            intent_score = 2 if "*" in i else (5 if intent in i else 0)
            if domain_score == 0 or intent_score == 0:
                continue
            authority_score = {
                "primary": 5,
                "institutional": 4,
                "peer_reviewed": 4,
                "scholarly_index": 3,
                "preprint": 2,
                "community": 1,
            }.get(spec.authority, 1)
            scored.append((domain_score + intent_score + authority_score, spec))
        scored.sort(key=lambda x: (-x[0], x[1].name.casefold()))
        return [s for _, s in scored]
