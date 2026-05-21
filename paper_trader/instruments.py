"""
KiteConnect instruments CSV download, caching, and lookup.

Downloads the full NFO instruments list once per day (configurable).
Cached locally as instruments_cache.csv — resolves:
  - Strike intervals per underlying
  - Available expiry dates
  - Exact instrument tokens for option contracts
"""
import logging
import time
from datetime import date, timedelta
from typing import Optional, Tuple

import pandas as pd

from paper_trader.config import (
    INSTRUMENTS_CACHE_PATH,
    INSTRUMENTS_REFRESH_HOURS,
)

logger = logging.getLogger("paper_trader.instruments")

# Strike intervals for indices (definitive values)
_INDEX_INTERVALS: dict[str, int] = {
    "NIFTY":      50,
    "BANKNIFTY":  100,
    "FINNIFTY":   50,
    "MIDCPNIFTY": 25,
}

_df: Optional[pd.DataFrame] = None
_loaded_at: float = 0.0


# ── Load / refresh ────────────────────────────────────────────────────────────

def _needs_refresh() -> bool:
    if not INSTRUMENTS_CACHE_PATH.exists():
        return True
    age_h = (time.time() - INSTRUMENTS_CACHE_PATH.stat().st_mtime) / 3600
    return age_h >= INSTRUMENTS_REFRESH_HOURS


def load(kite) -> pd.DataFrame:
    """Return instruments DataFrame, downloading fresh if cache is stale."""
    global _df, _loaded_at

    in_memory_age_h = (time.time() - _loaded_at) / 3600
    if _df is not None and in_memory_age_h < INSTRUMENTS_REFRESH_HOURS:
        return _df

    if _needs_refresh():
        try:
            logger.info("[Instruments] Downloading NFO instruments from Kite...")
            rows = kite.instruments("NFO")
            fresh = pd.DataFrame(rows)
            INSTRUMENTS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            fresh.to_csv(INSTRUMENTS_CACHE_PATH, index=False)
            logger.info(f"[Instruments] Saved {len(fresh)} rows → {INSTRUMENTS_CACHE_PATH}")
        except Exception as exc:
            logger.warning(f"[Instruments] Download failed ({exc}) — using cached file")
            if not INSTRUMENTS_CACHE_PATH.exists():
                raise RuntimeError(
                    "No instruments cache and Kite download failed — cannot continue"
                ) from exc

    df = pd.read_csv(INSTRUMENTS_CACHE_PATH, low_memory=False)
    df["expiry"] = pd.to_datetime(df["expiry"], errors="coerce").dt.date
    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")

    _df = df
    _loaded_at = time.time()
    logger.info(f"[Instruments] Loaded {len(_df)} rows from cache")
    return _df


# ── Strike interval ───────────────────────────────────────────────────────────

def get_strike_interval(symbol: str) -> int:
    """Return the strike interval for the given underlying."""
    if symbol in _INDEX_INTERVALS:
        return _INDEX_INTERVALS[symbol]

    # Infer from available strikes in the instruments list
    if _df is not None:
        chain = _df[
            (_df["name"] == symbol) &
            (_df["instrument_type"].isin(["CE", "PE"]))
        ]
        if not chain.empty:
            strikes = sorted(chain["strike"].dropna().unique())
            if len(strikes) >= 2:
                diffs = [
                    strikes[i + 1] - strikes[i]
                    for i in range(min(10, len(strikes) - 1))
                ]
                interval = min(d for d in diffs if d > 0)
                return int(interval)

    return 5  # conservative fallback


# ── Expiry resolution ─────────────────────────────────────────────────────────

def get_nearest_expiry(symbol: str, min_days: int = 1) -> Optional[date]:
    """
    Return the nearest weekly expiry with at least `min_days` remaining.
    If none found with min_days, returns the nearest expiry overall.
    """
    if _df is None:
        return None

    chain = _df[
        (_df["name"] == symbol) &
        (_df["instrument_type"].isin(["CE", "PE"])) &
        (_df["expiry"].notna())
    ]
    if chain.empty:
        return None

    today = date.today()
    cutoff = today + timedelta(days=min_days)
    expiries = sorted(chain["expiry"].unique())

    # Prefer expiries with enough days remaining
    valid = [e for e in expiries if e >= cutoff]
    if valid:
        return valid[0]

    # Fallback: nearest available
    future = [e for e in expiries if e >= today]
    return future[0] if future else None


# ── Token resolution ──────────────────────────────────────────────────────────

def resolve_token(
    symbol: str,
    strike: float,
    option_type: str,
    expiry: date,
) -> Optional[int]:
    """Return instrument_token for the exact option contract, or None."""
    if _df is None:
        return None

    mask = (
        (_df["name"] == symbol) &
        (_df["instrument_type"] == option_type) &
        (_df["strike"] == strike) &
        (_df["expiry"] == expiry)
    )
    matches = _df[mask]
    if matches.empty:
        logger.warning(
            f"[Instruments] Token not found: {symbol} {strike}{option_type} {expiry}"
        )
        return None
    return int(matches.iloc[0]["instrument_token"])


def pick_itm_strike(
    symbol: str,
    spot: float,
    option_type: str,
    expiry: date,
    itm_steps: int = 1,
) -> Optional[Tuple[float, int]]:
    """
    Find the ITM strike directly from available contracts for this expiry.

    For CALL (CE): picks the highest strike ≤ spot, then steps deeper by
    (itm_steps-1) intervals.
    For PUT  (PE): picks the lowest  strike ≥ spot, then steps deeper.

    Returns (strike, instrument_token) or None if no contracts found.
    """
    if _df is None:
        return None

    chain = _df[
        (_df["name"] == symbol) &
        (_df["instrument_type"] == option_type) &
        (_df["expiry"] == expiry) &
        (_df["strike"].notna())
    ].copy()

    if chain.empty:
        return None

    strikes = sorted(chain["strike"].unique())

    if option_type == "CE":
        # ITM for calls = strikes below spot; pick closest then step deeper
        itm = [s for s in strikes if s <= spot]
        if not itm:
            itm = strikes  # all OTM — take nearest
        itm.sort(reverse=True)
        idx = min(itm_steps - 1, len(itm) - 1)
        target = itm[idx]
    else:
        # ITM for puts = strikes above spot; pick closest then step deeper
        itm = [s for s in strikes if s >= spot]
        if not itm:
            itm = strikes  # all OTM — take nearest
        itm.sort()
        idx = min(itm_steps - 1, len(itm) - 1)
        target = itm[idx]

    mask = (
        (_df["name"] == symbol) &
        (_df["instrument_type"] == option_type) &
        (_df["expiry"] == expiry) &
        (_df["strike"] == target)
    )
    row = _df[mask]
    if row.empty:
        return None
    return float(target), int(row.iloc[0]["instrument_token"])


def get_lot_size(symbol: str) -> int:
    """Return the lot size for the given underlying from instruments data."""
    if _df is None:
        return 1
    chain = _df[
        (_df["name"] == symbol) &
        (_df["instrument_type"].isin(["CE", "PE"]))
    ]
    if not chain.empty and "lot_size" in chain.columns:
        val = chain.iloc[0]["lot_size"]
        if pd.notna(val) and int(val) > 0:
            return int(val)
    return 1


def get_nse_spot_token(symbol: str, kite) -> Optional[int]:
    """
    Return the NSE spot instrument token for an underlying.
    Tries live instruments list first; falls back to config constants.
    """
    from paper_trader.config import INDEX_SPOT_TOKENS
    if symbol in INDEX_SPOT_TOKENS:
        return INDEX_SPOT_TOKENS[symbol]
    try:
        nse_instruments = kite.instruments("NSE")
        for inst in nse_instruments:
            if inst.get("tradingsymbol") == symbol and inst.get("instrument_type") == "EQ":
                return int(inst["instrument_token"])
    except Exception as exc:
        logger.warning(f"[Instruments] NSE token lookup failed for {symbol}: {exc}")
    return None
