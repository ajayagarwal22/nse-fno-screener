"""Layer 3 — Multi-Timeframe Technical Engine.

Top-down MTF approach:
  HTF (Daily)  → trend direction filter
  MTF (15-min) → RSI divergence detection (primary signal)
  LTF (5-min)  → entry confirmation (MACD cross, VWAP, volume, candle)
"""
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

try:
    import talib
    _TALIB = True
except ImportError:
    _TALIB = False


# ── Result dataclass ────────────────────────────────────────────────────────

@dataclass
class ConfluenceResult:
    score: float                        # 0–100 weighted score
    direction: str                      # BULLISH | BEARISH | NEUTRAL
    breakdown: dict                     # gate-level detail

    # HTF
    htf_trend: str                      # BULLISH | BEARISH | NEUTRAL
    htf_ema_aligned: bool

    # MTF — primary signal
    rsi_divergence: bool
    divergence_type: str                # BULLISH | BEARISH | NONE
    divergence_strength: float          # 0–1

    # LTF — confirmation
    vwap: float
    above_vwap: bool
    macd_hist: float
    macd_cross: bool                    # histogram just flipped sign
    volume_expansion: bool
    candle_confirm: bool

    # Misc indicators (kept for display)
    rsi: float                          # LTF RSI (current)
    atr: float
    ema20: float
    ema50: float
    ema200: float
    supertrend_bullish: bool


# ── RSI helper ──────────────────────────────────────────────────────────────

def _compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    if _TALIB:
        return pd.Series(
            talib.RSI(close.values.astype(float), timeperiod=period),
            index=close.index,
        )
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(span=period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(span=period, adjust=False).mean()
    return 100 - (100 / (1 + gain / loss.replace(0, np.nan)))


# ── Pivot detection ─────────────────────────────────────────────────────────

def _swing_lows(series: pd.Series, left: int = 3, right: int = 2) -> list[tuple[int, float]]:
    """Return (bar_index, value) for each confirmed swing low."""
    vals = series.values
    result = []
    for i in range(left, len(vals) - right):
        if np.isnan(vals[i]):
            continue
        if all(vals[i] <= vals[i - j] for j in range(1, left + 1)) and \
           all(vals[i] <= vals[i + j] for j in range(1, right + 1)):
            result.append((i, float(vals[i])))
    return result


def _swing_highs(series: pd.Series, left: int = 3, right: int = 2) -> list[tuple[int, float]]:
    """Return (bar_index, value) for each confirmed swing high."""
    vals = series.values
    result = []
    for i in range(left, len(vals) - right):
        if np.isnan(vals[i]):
            continue
        if all(vals[i] >= vals[i - j] for j in range(1, left + 1)) and \
           all(vals[i] >= vals[i + j] for j in range(1, right + 1)):
            result.append((i, float(vals[i])))
    return result


def _nearest_rsi_pivot(rsi_pivots: list[tuple[int, float]], price_idx: int, window: int = 6):
    """Find the closest RSI pivot to a given price pivot index."""
    candidates = [(i, v) for i, v in rsi_pivots if abs(i - price_idx) <= window]
    if not candidates:
        return None
    return min(candidates, key=lambda x: abs(x[0] - price_idx))[1]


# ── Divergence detection ────────────────────────────────────────────────────

def detect_bullish_divergence(
    df: pd.DataFrame,
    rsi_period: int = 14,
    pivot_left: int = 3,
    pivot_right: int = 2,
    lookback: int = 60,
) -> tuple[bool, float]:
    """
    Classic bullish RSI divergence on MTF (15-min) data.
    Price makes lower low → RSI makes higher low → bullish reversal signal.
    Returns (detected, strength 0-1).
    """
    if df is None or len(df) < lookback:
        return False, 0.0

    df = df.tail(lookback).reset_index(drop=True)
    close = df["close"]
    rsi = _compute_rsi(close, rsi_period)

    price_lows = _swing_lows(close, pivot_left, pivot_right)
    rsi_lows = _swing_lows(rsi, pivot_left, pivot_right)

    if len(price_lows) < 2 or len(rsi_lows) < 2:
        return False, 0.0

    # Compare last two price swing lows
    (i1, p1), (i2, p2) = price_lows[-2], price_lows[-1]

    # Price must be making a lower low
    if p2 >= p1:
        return False, 0.0

    # RSI at corresponding bars must be making higher low
    r1 = _nearest_rsi_pivot(rsi_lows, i1)
    r2 = _nearest_rsi_pivot(rsi_lows, i2)
    if r1 is None or r2 is None or r2 <= r1:
        return False, 0.0

    # Divergence confirmed — compute strength
    price_drop_pct = (p1 - p2) / p1
    rsi_recovery = (r2 - r1) / max(abs(r1), 1)
    strength = min(1.0, (price_drop_pct * 10 + rsi_recovery * 5) / 2)

    # Penalise if RSI not in oversold/low zone (divergence more meaningful <50)
    if r2 > 55:
        strength *= 0.6

    return True, round(strength, 3)


def detect_bearish_divergence(
    df: pd.DataFrame,
    rsi_period: int = 14,
    pivot_left: int = 3,
    pivot_right: int = 2,
    lookback: int = 60,
) -> tuple[bool, float]:
    """
    Classic bearish RSI divergence on MTF (15-min) data.
    Price makes higher high → RSI makes lower high → bearish reversal signal.
    Returns (detected, strength 0-1).
    """
    if df is None or len(df) < lookback:
        return False, 0.0

    df = df.tail(lookback).reset_index(drop=True)
    close = df["close"]
    rsi = _compute_rsi(close, rsi_period)

    price_highs = _swing_highs(close, pivot_left, pivot_right)
    rsi_highs = _swing_highs(rsi, pivot_left, pivot_right)

    if len(price_highs) < 2 or len(rsi_highs) < 2:
        return False, 0.0

    (i1, p1), (i2, p2) = price_highs[-2], price_highs[-1]

    # Price must be making a higher high
    if p2 <= p1:
        return False, 0.0

    r1 = _nearest_rsi_pivot(rsi_highs, i1)
    r2 = _nearest_rsi_pivot(rsi_highs, i2)
    if r1 is None or r2 is None or r2 >= r1:
        return False, 0.0

    price_rise_pct = (p2 - p1) / p1
    rsi_drop = (r1 - r2) / max(abs(r1), 1)
    strength = min(1.0, (price_rise_pct * 10 + rsi_drop * 5) / 2)

    if r2 < 45:
        strength *= 0.6

    return True, round(strength, 3)


# ── HTF trend ───────────────────────────────────────────────────────────────

def assess_htf_trend(df_daily: Optional[pd.DataFrame]) -> tuple[str, bool]:
    """
    Assess daily-chart trend. Returns (trend: str, ema_aligned: bool).
    trend = BULLISH | BEARISH | NEUTRAL
    """
    if df_daily is None or len(df_daily) < 20:
        return "NEUTRAL", False

    close = df_daily["close"]
    ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
    ema200 = close.ewm(span=min(200, len(close)), adjust=False).mean().iloc[-1]
    cur = close.iloc[-1]

    bull_signals = sum([
        cur > ema20,
        cur > ema50,
        ema20 > ema50,
        ema50 > ema200,
        cur > close.iloc[-20],          # 20-day momentum
    ])

    ema_aligned = ema20 > ema50 > ema200

    if bull_signals >= 4:
        return "BULLISH", ema_aligned
    elif bull_signals <= 1:
        return "BEARISH", False
    return "NEUTRAL", ema_aligned


# ── LTF confirmation (5-min) ────────────────────────────────────────────────

def _compute_vwap(df: pd.DataFrame) -> float:
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol = df["volume"].cumsum().replace(0, np.nan)
    return float((typical * df["volume"]).cumsum().iloc[-1] / cum_vol.iloc[-1])


def _atr(df: pd.DataFrame, period: int = 14) -> float:
    if _TALIB:
        return float(talib.ATR(df["high"].values, df["low"].values,
                               df["close"].values, timeperiod=period)[-1])
    high, low, close = df["high"], df["low"], df["close"]
    tr = pd.concat([high - low, (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    return float(tr.ewm(span=period, adjust=False).mean().iloc[-1])


def _supertrend_bullish(df: pd.DataFrame, period: int = 10, mult: float = 3.0) -> bool:
    hl2 = (df["high"] + df["low"]) / 2
    if _TALIB:
        atr_s = pd.Series(talib.ATR(df["high"].values, df["low"].values,
                                    df["close"].values, timeperiod=period), index=df.index)
    else:
        high, low, close = df["high"], df["low"], df["close"]
        tr = pd.concat([high - low, (high - close.shift()).abs(),
                        (low - close.shift()).abs()], axis=1).max(axis=1)
        atr_s = tr.ewm(span=period, adjust=False).mean()

    ub = hl2 + mult * atr_s
    lb = hl2 - mult * atr_s
    bullish = True
    st = lb.iloc[0]
    for i in range(1, len(df)):
        prev_close = df["close"].iloc[i - 1]
        cur_close  = df["close"].iloc[i]
        new_ub = ub.iloc[i] if (ub.iloc[i] < ub.iloc[i-1] or prev_close > ub.iloc[i-1]) else ub.iloc[i-1]
        new_lb = lb.iloc[i] if (lb.iloc[i] > lb.iloc[i-1] or prev_close < lb.iloc[i-1]) else lb.iloc[i-1]
        if bullish:
            st = new_lb
            if cur_close < new_lb:
                bullish = False; st = new_ub
        else:
            st = new_ub
            if cur_close > new_ub:
                bullish = True; st = new_lb
    return bullish


def score_ltf_confirmation(df: pd.DataFrame) -> dict:
    """LTF (5-min) entry confirmation indicators."""
    if df is None or len(df) < 26:
        return {}

    close = df["close"]
    s = close.values.astype(float)
    ser = pd.Series(s)

    # MACD
    ema12 = ser.ewm(span=12, adjust=False).mean()
    ema26 = ser.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    hist = macd_line - signal_line
    macd_hist = float(hist.iloc[-1])
    macd_hist_prev = float(hist.iloc[-2]) if len(hist) >= 2 else 0.0
    macd_bull_cross = macd_hist > 0 and macd_hist_prev <= 0
    macd_bear_cross = macd_hist < 0 and macd_hist_prev >= 0

    # VWAP
    vwap = _compute_vwap(df)
    cur = float(s[-1])
    above_vwap = cur > vwap

    # Volume expansion vs last 20 bars
    vol = df["volume"].values
    avg_vol = np.mean(vol[-21:-1]) if len(vol) > 21 else np.mean(vol[:-1])
    vol_expansion = bool(vol[-1] > avg_vol * 1.3) if avg_vol > 0 else False

    # Candle confirmation (last bar)
    o = float(df["open"].iloc[-1])
    h = float(df["high"].iloc[-1])
    l = float(df["low"].iloc[-1])
    c = float(df["close"].iloc[-1])
    rng = h - l
    bull_candle = (c > o and rng > 0 and (c - o) / rng > 0.5)
    bear_candle = (c < o and rng > 0 and (o - c) / rng > 0.5)

    # RSI on LTF
    rsi_series = _compute_rsi(close)
    rsi_val = float(rsi_series.iloc[-1]) if not rsi_series.empty else 50.0

    # EMAs on LTF (for display)
    ema20 = float(ser.ewm(span=20, adjust=False).mean().iloc[-1])
    ema50 = float(ser.ewm(span=50, adjust=False).mean().iloc[-1])
    ema200 = float(ser.ewm(span=200, adjust=False).mean().iloc[-1]) if len(ser) >= 50 else ema50

    atr_val = _atr(df) if len(df) >= 14 else 0.0
    st_bull = _supertrend_bullish(df) if len(df) >= 20 else True

    return {
        "macd_hist": macd_hist,
        "macd_bull_cross": macd_bull_cross,
        "macd_bear_cross": macd_bear_cross,
        "vwap": vwap,
        "above_vwap": above_vwap,
        "vol_expansion": vol_expansion,
        "bull_candle": bull_candle,
        "bear_candle": bear_candle,
        "rsi": rsi_val,
        "ema20": ema20, "ema50": ema50, "ema200": ema200,
        "atr": atr_val,
        "supertrend_bull": st_bull,
    }


# ── Main MTF scorers ────────────────────────────────────────────────────────

def score_bullish_confluence(
    df_ltf: Optional[pd.DataFrame],
    df_mtf: Optional[pd.DataFrame] = None,
    df_htf: Optional[pd.DataFrame] = None,
) -> "ConfluenceResult":
    """
    MTF bullish score.
    Primary: RSI divergence on MTF (15-min)
    Filter:  HTF trend bullish (daily)
    Confirm: MACD cross + VWAP + volume + candle on LTF (5-min)
    """
    # HTF trend
    htf_trend, htf_ema = assess_htf_trend(df_htf)

    # MTF RSI divergence — PRIMARY signal (pivot_right=0: detect on current bar, no lag)
    bull_div, div_strength = (
        detect_bullish_divergence(df_mtf, pivot_right=0, lookback=80)
        if df_mtf is not None else (False, 0.0)
    )

    # LTF confirmation
    ltf = score_ltf_confirmation(df_ltf) if df_ltf is not None else {}

    gates = {
        "htf_trend_bullish":  htf_trend == "BULLISH",
        "rsi_divergence":     bull_div,
        "macd_bull_cross":    ltf.get("macd_bull_cross", False),
        "above_vwap":         ltf.get("above_vwap", False),
        "volume_expansion":   ltf.get("vol_expansion", False),
        "candle_confirm":     ltf.get("bull_candle", False),
    }

    weights = {
        "htf_trend_bullish": 20,
        "rsi_divergence":    30,    # primary — highest weight
        "macd_bull_cross":   20,
        "above_vwap":        15,
        "volume_expansion":  10,
        "candle_confirm":     5,
    }

    raw_score = sum(weights[k] for k, v in gates.items() if v)
    direction = "BULLISH" if raw_score >= 50 else "NEUTRAL"

    return ConfluenceResult(
        score=float(raw_score), direction=direction, breakdown=gates,
        htf_trend=htf_trend, htf_ema_aligned=htf_ema,
        rsi_divergence=bull_div, divergence_type="BULLISH" if bull_div else "NONE",
        divergence_strength=div_strength,
        vwap=ltf.get("vwap", 0.0), above_vwap=ltf.get("above_vwap", False),
        macd_hist=ltf.get("macd_hist", 0.0), macd_cross=ltf.get("macd_bull_cross", False),
        volume_expansion=ltf.get("vol_expansion", False), candle_confirm=ltf.get("bull_candle", False),
        rsi=ltf.get("rsi", 50.0), atr=ltf.get("atr", 0.0),
        ema20=ltf.get("ema20", 0.0), ema50=ltf.get("ema50", 0.0), ema200=ltf.get("ema200", 0.0),
        supertrend_bullish=ltf.get("supertrend_bull", False),
    )


def score_bearish_confluence(
    df_ltf: Optional[pd.DataFrame],
    df_mtf: Optional[pd.DataFrame] = None,
    df_htf: Optional[pd.DataFrame] = None,
) -> "ConfluenceResult":
    """
    MTF bearish score.
    Primary: RSI divergence on MTF (15-min)
    Filter:  HTF trend bearish (daily)
    Confirm: MACD cross + VWAP + volume + candle on LTF (5-min)
    """
    htf_trend, htf_ema = assess_htf_trend(df_htf)

    # MTF RSI divergence — PRIMARY signal (pivot_right=0: detect on current bar, no lag)
    bear_div, div_strength = (
        detect_bearish_divergence(df_mtf, pivot_right=0, lookback=80)
        if df_mtf is not None else (False, 0.0)
    )

    ltf = score_ltf_confirmation(df_ltf) if df_ltf is not None else {}

    gates = {
        "htf_trend_bearish":  htf_trend == "BEARISH",
        "rsi_divergence":     bear_div,
        "macd_bear_cross":    ltf.get("macd_bear_cross", False),
        "below_vwap":         not ltf.get("above_vwap", True),
        "volume_expansion":   ltf.get("vol_expansion", False),
        "candle_confirm":     ltf.get("bear_candle", False),
    }

    weights = {
        "htf_trend_bearish": 20,
        "rsi_divergence":    30,
        "macd_bear_cross":   20,
        "below_vwap":        15,
        "volume_expansion":  10,
        "candle_confirm":     5,
    }

    raw_score = sum(weights[k] for k, v in gates.items() if v)
    direction = "BEARISH" if raw_score >= 50 else "NEUTRAL"

    return ConfluenceResult(
        score=float(raw_score), direction=direction, breakdown=gates,
        htf_trend=htf_trend, htf_ema_aligned=htf_ema,
        rsi_divergence=bear_div, divergence_type="BEARISH" if bear_div else "NONE",
        divergence_strength=div_strength,
        vwap=ltf.get("vwap", 0.0), above_vwap=ltf.get("above_vwap", True),
        macd_hist=ltf.get("macd_hist", 0.0), macd_cross=ltf.get("macd_bear_cross", False),
        volume_expansion=ltf.get("vol_expansion", False), candle_confirm=ltf.get("bear_candle", False),
        rsi=ltf.get("rsi", 50.0), atr=ltf.get("atr", 0.0),
        ema20=ltf.get("ema20", 0.0), ema50=ltf.get("ema50", 0.0), ema200=ltf.get("ema200", 0.0),
        supertrend_bullish=ltf.get("supertrend_bull", False),
    )


def _empty_result(direction: str) -> ConfluenceResult:
    return ConfluenceResult(
        score=0.0, direction=direction, breakdown={},
        htf_trend="NEUTRAL", htf_ema_aligned=False,
        rsi_divergence=False, divergence_type="NONE", divergence_strength=0.0,
        vwap=0.0, above_vwap=False, macd_hist=0.0, macd_cross=False,
        volume_expansion=False, candle_confirm=False,
        rsi=50.0, atr=0.0, ema20=0.0, ema50=0.0, ema200=0.0, supertrend_bullish=False,
    )
