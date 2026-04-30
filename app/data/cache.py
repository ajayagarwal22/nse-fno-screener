from cachetools import TTLCache
from threading import Lock

_LTP_TTL = 30       # seconds
_OI_TTL = 60
_REGIME_TTL = 300

_ltp_cache: TTLCache = TTLCache(maxsize=500, ttl=_LTP_TTL)
_oi_cache: TTLCache = TTLCache(maxsize=200, ttl=_OI_TTL)
_regime_cache: TTLCache = TTLCache(maxsize=10, ttl=_REGIME_TTL)

_lock = Lock()


def get_ltp(symbol: str):
    with _lock:
        return _ltp_cache.get(symbol)


def set_ltp(symbol: str, value: float):
    with _lock:
        _ltp_cache[symbol] = value


def get_oi(key: str):
    with _lock:
        return _oi_cache.get(key)


def set_oi(key: str, value):
    with _lock:
        _oi_cache[key] = value


def get_regime(key: str = "market"):
    with _lock:
        return _regime_cache.get(key)


def set_regime(value, key: str = "market"):
    with _lock:
        _regime_cache[key] = value
