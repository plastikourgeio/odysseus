"""Isolated arxiv.py worker used by the Odysseus STEM source registry.

The third-party ``arxiv`` package is vendored under /app/data/stem_arxiv_vendor.
This worker is executed as a file next to our own connector module ``arxiv.py``;
without path isolation Python would import the local connector instead of the
third-party package.  Keep the vendor directory first and remove this script
folder from ``sys.path`` before importing ``arxiv``.
"""
from __future__ import annotations

import json
import os
import re
import sys
from importlib.metadata import version as package_version
from pathlib import Path


def _load_third_party_arxiv():
    script_dir = Path(__file__).resolve().parent
    vendor_dir = Path(
        os.getenv("STEM_ARXIV_VENDOR", "/app/data/stem_arxiv_vendor")
    ).resolve()

    # When Python executes a file directly, sys.path[0] is the script's
    # directory.  That directory contains our connector ``arxiv.py`` and would
    # shadow the third-party package.  Remove it explicitly.
    cleaned = []
    for entry in sys.path:
        try:
            resolved = Path(entry or os.getcwd()).resolve()
        except Exception:
            cleaned.append(entry)
            continue
        if resolved == script_dir:
            continue
        if resolved == vendor_dir:
            continue
        cleaned.append(entry)

    sys.path[:] = [str(vendor_dir)] + cleaned

    import arxiv as third_party_arxiv

    module_file = Path(getattr(third_party_arxiv, "__file__", "")).resolve()
    local_connector = (script_dir / "arxiv.py").resolve()
    if module_file == local_connector:
        raise RuntimeError(
            "arxiv import shadowed by Odysseus connector instead of vendored arxiv.py"
        )
    if vendor_dir not in module_file.parents:
        raise RuntimeError(
            f"arxiv imported from unexpected path: {module_file}; expected under {vendor_dir}"
        )
    return third_party_arxiv


arxiv = _load_third_party_arxiv()


def _arxiv_id(entry_id: str) -> str:
    value = (entry_id or "").rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r"v\d+$", "", value)


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1] == "--self-test":
        payload = {
            "version": package_version("arxiv"),
            "module_file": str(Path(arxiv.__file__).resolve()),
        }
        sys.stdout.write(json.dumps(payload, ensure_ascii=False))
        return 0

    if len(sys.argv) < 3:
        print("usage: arxiv_worker.py QUERY LIMIT", file=sys.stderr)
        return 2

    query = sys.argv[1]
    limit = max(1, min(20, int(sys.argv[2])))
    client = arxiv.Client(
        page_size=limit,
        delay_seconds=3.1,
        num_retries=2,
    )
    search = arxiv.Search(
        query=query,
        max_results=limit,
        sort_by=arxiv.SortCriterion.Relevance,
    )

    rows = []
    for result in client.results(search):
        categories = list(getattr(result, "categories", None) or [])
        primary_category = getattr(result, "primary_category", None)
        if hasattr(primary_category, "term"):
            primary_category = primary_category.term
        authors = [str(a) for a in (getattr(result, "authors", None) or [])]
        published = getattr(result, "published", None)
        rows.append({
            "title": " ".join((getattr(result, "title", "") or "").split()),
            "summary": " ".join((getattr(result, "summary", "") or "").split()),
            "entry_id": getattr(result, "entry_id", "") or "",
            "pdf_url": getattr(result, "pdf_url", "") or "",
            "arxiv_id": _arxiv_id(getattr(result, "entry_id", "") or ""),
            "authors": authors,
            "published": published.isoformat() if published else "",
            "doi": getattr(result, "doi", None),
            "primary_category": primary_category,
            "categories": categories,
            "comment": getattr(result, "comment", None),
            "journal_ref": getattr(result, "journal_ref", None),
        })

    sys.stdout.write(json.dumps(rows, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
