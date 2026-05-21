"""Layer 2 — Stock Selection Engine.

Filters the full NSE F&O universe to ranked long/short candidates based on
relative strength, volume expansion, option chain liquidity, and OI quality.
"""
import time
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

from app.config import settings
from app.data.kite_client import kite_client
from app.engines.market_regime import Bias, MarketRegime

# Option chain liquidity changes slowly — cache result per symbol for 30 min
_liquidity_cache: dict[str, tuple[bool, float]] = {}  # symbol -> (is_liquid, ts)
_LIQUIDITY_TTL = 1800

# NSE indices with F&O on NSE — screened for option signals.
# SENSEX is shown in the display panel but has BFO options (not NFO) so excluded here.
_NSE_INDEX_CONFIGS: list[tuple[str, str]] = [
    # (option_chain_name,  nse_tradingsymbol)
    ("NIFTY",     "NIFTY 50"),
    ("BANKNIFTY", "NIFTY BANK"),
    ("FINNIFTY",  "NIFTY FIN SERVICE"),
]


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
    is_index: bool = False   # True for index candidates (NIFTY, BANKNIFTY, FINNIFTY)


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
    """Check option chain liquidity. Result cached for 30 min — liquidity
    rarely changes intraday and this avoids 200 quote calls per scan."""
    cached = _liquidity_cache.get(symbol)
    if cached and time.time() - cached[1] < _LIQUIDITY_TTL:
        return cached[0]
    try:
        chain = kite_client.get_option_chain(symbol)
        if chain.empty:
            result = False
        else:
            atm_rows = chain.nlargest(10, "oi")
            if atm_rows["oi"].max() < settings.min_oi_threshold:
                result = False
            else:
                atm_rows = atm_rows[atm_rows["ltp"] > 0]
                if atm_rows.empty:
                    result = False
                else:
                    spread_pct = ((atm_rows["ask"] - atm_rows["bid"]) / atm_rows["ltp"].clip(lower=0.01)) * 100
                    result = spread_pct.median() < settings.max_bid_ask_spread_pct * 5
    except Exception:
        result = False
    _liquidity_cache[symbol] = (result, time.time())
    return result


def _build_index_candidates(
    regime: MarketRegime,
    nifty_daily_df: pd.DataFrame | None,
) -> list[StockCandidate]:
    """
    Always-included index candidates (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY).
    Spot tokens fetched from Kite NSE instruments list so we never hardcode them.
    Direction: computed from RS vs Nifty, except for NIFTY itself which uses regime bias.
    """
    index_tokens = kite_client.get_nse_index_tokens()

    nifty_ret = pd.Series(dtype=float)
    if nifty_daily_df is not None and not nifty_daily_df.empty:
        nifty_ret = nifty_daily_df["close"].pct_change().dropna().tail(20)

    candidates: list[StockCandidate] = []

    for symbol, nse_sym in _NSE_INDEX_CONFIGS:
        token = index_tokens.get(nse_sym)
        if token is None:
            continue
        try:
            df = kite_client.get_ohlcv(token, interval="day")
        except Exception:
            continue
        if df is None or len(df) < 21:
            continue

        index_ret = df["close"].pct_change().dropna().tail(20)

        if symbol == "NIFTY":
            # RS vs itself is always 0 — use regime's nifty bias instead
            if regime.nifty_bias == Bias.BULLISH:
                rs, candidacy = 0.03, Candidacy.BULLISH
                reason = "Nifty 50 — regime bullish"
            elif regime.nifty_bias == Bias.BEARISH:
                rs, candidacy = -0.03, Candidacy.BEARISH
                reason = "Nifty 50 — regime bearish"
            else:
                continue
        else:
            rs = _relative_strength(index_ret, nifty_ret)
            if rs > 0.02:
                candidacy = Candidacy.BULLISH
                reason = f"{nse_sym} RS={rs:+.2%} — outperforming Nifty"
            elif rs < -0.02:
                candidacy = Candidacy.BEARISH
                reason = f"{nse_sym} RS={rs:+.2%} — underperforming Nifty"
            else:
                continue

        _, vol_ratio = _volume_expansion(df, 1.0)
        candidates.append(StockCandidate(
            symbol=symbol,
            instrument_token=token,
            candidacy=candidacy,
            rs_score=round(rs, 4),
            volume_ratio=vol_ratio,
            option_liquidity=True,   # index options are always liquid
            reason=reason,
            is_index=True,
        ))

    return candidates


def select_candidates(
    regime: MarketRegime,
    nifty_daily_df: pd.DataFrame | None = None,
    top_n: int = 20,
) -> list[StockCandidate]:
    """
    Return the top N bullish and top N bearish candidates from the F&O universe.
    Needs daily OHLCV data per symbol; gracefully skips symbols without data.
    """
    # Index names handled separately via _build_index_candidates — exclude them
    # here so NIFTY/BANKNIFTY/FINNIFTY don't appear twice in the same scan
    # (once via spot token, once via their NFO-FUT futures token).
    _index_names = {sym for sym, _ in _NSE_INDEX_CONFIGS}

    instruments = kite_client.get_fno_instruments()
    stocks = instruments[
        ((instruments["segment"] == "NFO-FUT") | (instruments["instrument_type"] == "EQ")) &
        (~instruments["name"].isin(_index_names))
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
        # Skip daily volume gate intraday — today's bar is incomplete so it always
        # reads below the 20-day average. Layer 3 checks intraday volume on 5-min data.
        _, vol_ratio = _volume_expansion(df, settings.min_volume_multiplier)

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

    # Index candidates always prepended — they are highest priority and most liquid
    index_cands = _build_index_candidates(regime, nifty_daily_df)
    return index_cands + bullish + bearish
