"""Layer 2 — Stock Selection Engine.

Filters the full NSE F&O universe to ranked long/short candidates based on
relative strength, volume expansion, option chain liquidity, and OI quality.
"""
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

from app.config import settings
from app.data.kite_client import kite_client
from app.engines.market_regime import Bias, MarketRegime


class Candidacy(str, Enum):
    BULLISH = "BULLISH_CANDIDATE"
    BEARISH = "BEARISH_CANDIDATE"
    NEUTRAL = "NEUTRAL"


@dataclass
class StockCandidate:
    symbol: str
    instrument_token: int
    candidacy: Candidacy
    rs_score: float          # relative strength vs Nifty; positive = outperforming
    volume_ratio: float      # today's volume / 20-day avg volume
    option_liquidity: bool   # meets min OI + spread requirements
    reason: str


def _relative_strength(stock_returns: pd.Series, nifty_returns: pd.Series) -> float:
    """Simple RS: cumulative stock return minus cumulative Nifty return over period."""
    if stock_returns.empty or nifty_returns.empty:
        return 0.0
    return float(stock_returns.sum() - nifty_returns.sum())


def _volume_expansion(df: pd.DataFrame, multiplier: float) -> tuple[bool, float]:
    """Check if today's volume is >= multiplier × 20-day average."""
    if df is None or len(df) < 21:
        return False, 1.0
    avg_volume = df["volume"].iloc[-21:-1].mean()
    if avg_volume == 0:
        return False, 1.0
    ratio = df["volume"].iloc[-1] / avg_volume
    return ratio >= multiplier, round(ratio, 2)


def _check_option_liquidity(symbol: str) -> bool:
    """Quick check: does the nearest expiry option chain meet OI + spread thresholds?"""
    try:
        chain = kite_client.get_option_chain(symbol)
        if chain.empty:
            return False
        atm_rows = chain.nlargest(10, "oi")
        if atm_rows["oi"].max() < settings.min_oi_threshold:
            return False
        atm_rows = atm_rows[atm_rows["ltp"] > 0]
        if atm_rows.empty:
            return False
        spread_pct = ((atm_rows["ask"] - atm_rows["bid"]) / atm_rows["ltp"].clip(lower=0.01)) * 100
        return spread_pct.median() < settings.max_bid_ask_spread_pct * 5
    except Exception:
        return False


def select_candidates(
    regime: MarketRegime,
    nifty_daily_df: pd.DataFrame | None = None,
    top_n: int = 20,
) -> list[StockCandidate]:
    """
    Return the top N bullish and top N bearish candidates from the F&O universe.
    Needs daily OHLCV data per symbol; gracefully skips symbols without data.
    """
    instruments = kite_client.get_fno_instruments()
    stocks = instruments[
        (instruments["segment"] == "NFO-FUT") | (instruments["instrument_type"] == "EQ")
    ].drop_duplicates(subset=["name"])

    nifty_ret = pd.Series(dtype=float)
    if nifty_daily_df is not None and not nifty_daily_df.empty:
        nifty_ret = nifty_daily_df["close"].pct_change().dropna().tail(20)

    candidates: list[StockCandidate] = []

    for _, row in stocks.iterrows():
        symbol = row["name"]
        token = int(row["instrument_token"])

        try:
            df = kite_client.get_ohlcv(token, interval="day")
        except Exception:
            continue

        if df is None or len(df) < 21:
            continue

        stock_ret = df["close"].pct_change().dropna().tail(20)
        rs = _relative_strength(stock_ret, nifty_ret)
        vol_ok, vol_ratio = _volume_expansion(df, settings.min_volume_multiplier)

        if not vol_ok:
            continue

        opt_liquid = _check_option_liquidity(symbol)

        if rs > 0.02:
            candidacy = Candidacy.BULLISH
            reason = f"RS={rs:+.2%}, vol={vol_ratio:.1f}x, outperforming Nifty"
        elif rs < -0.02:
            candidacy = Candidacy.BEARISH
            reason = f"RS={rs:+.2%}, vol={vol_ratio:.1f}x, underperforming Nifty"
        else:
            continue

        candidates.append(StockCandidate(
            symbol=symbol,
            instrument_token=token,
            candidacy=candidacy,
            rs_score=round(rs, 4),
            volume_ratio=vol_ratio,
            option_liquidity=opt_liquid,
            reason=reason,
        ))

    bullish = sorted(
        [c for c in candidates if c.candidacy == Candidacy.BULLISH],
        key=lambda x: x.rs_score,
        reverse=True,
    )[:top_n]

    bearish = sorted(
        [c for c in candidates if c.candidacy == Candidacy.BEARISH],
        key=lambda x: x.rs_score,
    )[:top_n]

    return bullish + bearish
