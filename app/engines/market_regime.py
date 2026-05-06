"""Layer 1 — Market Regime Engine.

Classifies the overall market state by combining India VIX, index structure,
VWAP positioning, and breadth. All downstream engines gate on this output.
"""
from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

from app.data import cache
from app.data.nse_client import MarketBreadth, VIXData, fetch_market_breadth, fetch_vix


class RegimeType(str, Enum):
    TRENDING_BULLISH = "TRENDING_BULLISH"
    TRENDING_BEARISH = "TRENDING_BEARISH"
    RANGEBOUND = "RANGEBOUND"
    MEAN_REVERTING = "MEAN_REVERTING"
    HIGH_VOL_EXPANSION = "HIGH_VOL_EXPANSION"
    EVENT_RISK = "EVENT_RISK"
    THETA_DECAY = "THETA_DECAY"


class Bias(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


@dataclass
class MarketRegime:
    regime_type: RegimeType
    vix_data: VIXData
    breadth: MarketBreadth
    nifty_bias: Bias
    banknifty_bias: Bias
    overall_bias: Bias
    call_buying_environment: bool
    put_buying_environment: bool
    reason: str


def _compute_vwap(df: pd.DataFrame) -> float:
    """Intraday VWAP from OHLCV dataframe."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    total_vol = df["volume"].sum()
    if total_vol == 0:
        return float(df["close"].iloc[-1])
    return float((typical * df["volume"]).sum() / total_vol)


def _ema_slope(series: pd.Series, period: int) -> float:
    ema = series.ewm(span=period, adjust=False).mean()
    if len(ema) < 2:
        return 0.0
    return float(ema.iloc[-1] - ema.iloc[-2])


def _assess_index_bias(df: pd.DataFrame) -> Bias:
    """Determine directional bias for a single index from OHLCV data."""
    if df is None or df.empty or len(df) < 20:
        return Bias.NEUTRAL

    close = df["close"]
    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
    current = close.iloc[-1]

    vwap = _compute_vwap(df)
    slope20 = _ema_slope(close, 20)

    bullish_signals = sum([
        current > ema20,
        current > ema50,
        ema20 > ema50,
        current > vwap,
        slope20 > 0,
    ])

    if bullish_signals >= 4:
        return Bias.BULLISH
    elif bullish_signals <= 1:
        return Bias.BEARISH
    return Bias.NEUTRAL


def _classify_regime(
    vix: VIXData,
    breadth: MarketBreadth,
    nifty_bias: Bias,
    banknifty_bias: Bias,
) -> tuple[RegimeType, str]:
    reasons = []

    if vix.value > 25:
        return RegimeType.HIGH_VOL_EXPANSION, f"VIX={vix.value:.1f} (extreme) — avoid option buying"

    if vix.value > 18:
        reasons.append(f"VIX={vix.value:.1f} elevated")

    if vix.change_pct > 5:
        return RegimeType.HIGH_VOL_EXPANSION, f"VIX spiking +{vix.change_pct:.1f}% — momentum opportunity"

    both_bullish = nifty_bias == Bias.BULLISH and banknifty_bias == Bias.BULLISH
    both_bearish = nifty_bias == Bias.BEARISH and banknifty_bias == Bias.BEARISH
    divergent = nifty_bias != banknifty_bias

    if both_bullish and breadth.breadth_score > 60:
        reasons.append(f"breadth={breadth.breadth_score:.0f}% advancing")
        if vix.value < 13:
            reasons.append(f"low VIX={vix.value:.1f} — premiums cheap")
        return RegimeType.TRENDING_BULLISH, "; ".join(reasons)

    if both_bearish and breadth.breadth_score < 40:
        reasons.append(f"breadth={breadth.breadth_score:.0f}% declining")
        return RegimeType.TRENDING_BEARISH, "; ".join(reasons)

    if divergent:
        return RegimeType.RANGEBOUND, "Nifty/BankNifty divergence — mixed signals"

    if 40 <= breadth.breadth_score <= 60:
        if vix.value < 13:
            return RegimeType.THETA_DECAY, f"low VIX={vix.value:.1f} + neutral breadth — theta environment"
        return RegimeType.RANGEBOUND, "Neutral breadth + no directional conviction"

    return RegimeType.MEAN_REVERTING, "No clear trend; mean-reversion conditions"


def analyze_market_regime(
    nifty_df: pd.DataFrame | None = None,
    banknifty_df: pd.DataFrame | None = None,
    is_event_day: bool = False,
) -> MarketRegime:
    """
    Main entry point for Layer 1. Accepts optional OHLCV DataFrames for
    Nifty and BankNifty; fetches VIX and breadth from NSE.
    """
    vix = fetch_vix()
    breadth = fetch_market_breadth()

    nifty_bias = _assess_index_bias(nifty_df)
    banknifty_bias = _assess_index_bias(banknifty_df)

    if is_event_day:
        regime_type = RegimeType.EVENT_RISK
        reason = "Major economic event scheduled — signals suppressed"
    else:
        regime_type, reason = _classify_regime(vix, breadth, nifty_bias, banknifty_bias)

    overall_bullish = sum([
        nifty_bias == Bias.BULLISH,
        banknifty_bias == Bias.BULLISH,
        breadth.breadth_score > 55,
        vix.value < 18,
    ])
    if overall_bullish >= 3:
        overall_bias = Bias.BULLISH
    elif overall_bullish <= 1:
        overall_bias = Bias.BEARISH
    else:
        overall_bias = Bias.NEUTRAL

    # Only suppress option buying for events (IV crush risk) and theta decay (premiums too costly).
    # MEAN_REVERTING and RANGEBOUND are prime environments for RSI divergence setups.
    _no_buy = {RegimeType.EVENT_RISK, RegimeType.THETA_DECAY}

    call_buying = (
        regime_type not in _no_buy
        and vix.value < 22
        and overall_bias != Bias.BEARISH
    )
    put_buying = (
        regime_type not in _no_buy
        and overall_bias != Bias.BULLISH
    )

    regime = MarketRegime(
        regime_type=regime_type,
        vix_data=vix,
        breadth=breadth,
        nifty_bias=nifty_bias,
        banknifty_bias=banknifty_bias,
        overall_bias=overall_bias,
        call_buying_environment=call_buying,
        put_buying_environment=put_buying,
        reason=reason,
    )
    cache.set_regime(regime)
    return regime
