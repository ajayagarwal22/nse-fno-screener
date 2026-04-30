import pytest


@pytest.fixture(autouse=True)
def reset_lru_cache():
    """Clear LRU caches between tests to prevent state leakage."""
    yield
    from app.data.kite_client import KiteClient
    KiteClient.get_fno_instruments.cache_clear()
