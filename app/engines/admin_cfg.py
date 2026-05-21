"""
Lightweight cached reader for admin/admin_config.json.

Usage:
    from app.engines.admin_cfg import cfg
    val = cfg("layer6", "thresholds", "b_score", default=60)
"""
import json
import time
from pathlib import Path
from typing import Any

_ADMIN_CONFIG = Path(__file__).parent.parent.parent / "admin" / "admin_config.json"
_TTL = 60          # re-read file at most once per minute
_cache: dict = {}
_loaded_at: float = 0.0


def _load() -> dict:
    global _cache, _loaded_at
    if _cache and (time.time() - _loaded_at) < _TTL:
        return _cache
    try:
        with open(_ADMIN_CONFIG) as f:
            _cache = json.load(f)
        _loaded_at = time.time()
    except Exception:
        pass  # keep stale cache on read error
    return _cache


def cfg(*keys: str, default: Any = None) -> Any:
    """
    Read a nested key from admin_config.json.
    e.g. cfg("layer6", "thresholds", "b_score", default=60)
    Returns `default` if the file is missing or the key path doesn't exist.
    """
    data = _load()
    node = data
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return default
        node = node[k]
    return node
