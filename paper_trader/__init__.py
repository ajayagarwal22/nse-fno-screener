"""
paper_trader — module-level singleton API.

Initialise once at startup, then call on_signal() from anywhere:

    # startup (app/main.py lifespan):
    import paper_trader
    paper_trader.init(kite=kite_client.kite)

    # signal generation (app/screener.py, after signals.append(signal)):
    import paper_trader
    paper_trader.on_signal(signal)
"""
import logging
import logging.handlers
from typing import Optional

from paper_trader.config import LOG_PATH
from paper_trader.trader import PaperTrader

# ── File logger (paper_trader.log) ───────────────────────────────────────────
_handler = logging.handlers.RotatingFileHandler(
    LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_handler.setFormatter(
    logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
)
logging.getLogger("paper_trader").addHandler(_handler)
logging.getLogger("paper_trader").setLevel(logging.INFO)

# ── Module-level singleton ────────────────────────────────────────────────────
_instance: Optional[PaperTrader] = None


def init(kite) -> PaperTrader:
    """
    Initialise the paper trader with an authenticated KiteConnect instance.
    Safe to call multiple times — subsequent calls are no-ops.
    """
    global _instance
    if _instance is None:
        _instance = PaperTrader(kite=kite)
    return _instance


def restart_ticker(kite) -> None:
    """Restart the KiteTicker after a Kite token refresh. Call from auth callback."""
    if _instance is not None:
        _instance.restart_ticker(kite)


def on_signal(signal) -> None:
    """
    Forward a signal to the paper trader. Returns immediately.
    Silently ignored if init() has not been called.
    """
    if _instance is not None:
        _instance.on_signal(signal)


__all__ = ["PaperTrader", "init", "on_signal", "restart_ticker"]
