from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Optional

try:
    from core.constants import DATA_DIR
    _BASE = Path(DATA_DIR)
except Exception:
    _BASE = Path("/app/data")

CACHE_DIR = _BASE / "cache" / "stem_sources"


def _path_for(key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return CACHE_DIR / f"{digest}.json"


def load(key: str, ttl_seconds: int) -> Optional[Any]:
    path = _path_for(key)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - float(payload.get("created", 0)) > ttl_seconds:
            path.unlink(missing_ok=True)
            return None
        return payload.get("data")
    except FileNotFoundError:
        return None
    except Exception:
        return None


def save(key: str, data: Any) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _path_for(key)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"created": time.time(), "data": data}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)
    except Exception:
        pass
