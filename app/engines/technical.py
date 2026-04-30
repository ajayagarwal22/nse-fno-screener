"""Layer 3 — Technical Confluence Engine.

Indicators are never used in isolation. Each function returns a structured
confluence score (0–100) with a per-indicator breakdown.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

try:
    import talib
    _TALIB_AVAILABLE = True
except ImportError:
    _TALIB_AVAILABLE = False


@dataclass
class ConfluenceResult:
    score: float                          # 0–100
    direction: str                        # BULLISH | BEARISH | NEUTRAL
    breakdown: dict[str, bool | float]    # per-indicator signals
    vwap: float
    ema20: float
    ema50: float
    ema200: float
    rsi: float
    macd_hist: float
    atr: float
    supertrend_bullish: bool


# ---------------------------------------------------------------------------
# Indicator helpers
# ---------------------------------------------------------------------------

def _compute_vwap(df: pd.DataFrame) -> pd.Series:
    """Intraday VWAP anchored to current session (cumulative)."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_tp_vol = (typical * df["volume"]).cumsum()
    cum_vol = df["volume"].cumsum()
    return cum_tp_vol / cum_vol.replace(0, np.nan)


def _supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.Series:
    """ATR-based Supertrend. Returns boolean Series: True = bullish."""
    hl2 = (df["high"] + df["low"]) / 2

    if _TALIB_AVAILABLE:
        atr = pd.Series(
            talib.ATR(df["high"].values, df["low"].values, df["close"].values, timeperiod=period),
            index=df.index,
        )
    else:
        atr = _atr_fallback(df, period)

    upper_band = hl2 + multiplier * atr
    lower_band = hl2 - multiplier * atr

    supertrend = pd.Series(np.nan, index=df.index)
    bullish = pd.Series(True, index=df.index)

    for i in range(1, len(df)):
        prev_st = supertrend.iloc[i - 1]
        close = df["close"].iloc[i]
        prev_close = df["close"].iloc[i - 1]
        ub = upper_band.iloc[i]
        lb = lower_band.iloc[i]
        prev_ub = upper_band.iloc[i - 1]
        prev_lb = lower_band.iloc[i - 1]

        final_ub = ub if (ub < prev_ub or prev_close > prev_ub) else prev_ub
        final_lb = lb if (lb > prev_lb or prev_close < prev_lb) else prev_lb

        if np.isnan(prev_st):
            supertrend.iloc[i] = final_lb
            bullish.iloc[i] = True
        elif prev_st == prev_ub:
            if close <= final_ub:
                supertrend.iloc[i] = final_ub
                bullish.iloc[i] = False
            else:
                supertrend.iloc[i] = final_lb
                bullish.iloc[i] = True
        else:
            if close >= final_lb:
                supertrend.iloc[i] = final_lb
                bullish.iloc[i] = True
            else:
                supertrend.iloc[i] = final_ub
                bullish.iloc[i] = False

    return bullish


def _atr_fallback(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Pure-pandas ATR when TA-Lib is not installed."""
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def _compute_indicators(df: pd.DataFrame) -> dict:
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    volume = df["volume"].values

    if _TALIB_AVAILABLE:
        ema20 = talib.EMA(close, timeperiod=20)[-1]
        ema50 = talib.EMA(close, timeperiod=50)[-1]
        ema200 = talib.EMA(close, timeperiod=200)[-1]
        rsi = talib.RSI(close, timeperiod=14)[-1]
        macd_line, signal_line, hist = talib.MACD(close, fastperiod=12, slowperiod=26, signalperiod=9)
        macd_hist = hist[-1]
        macd_hist_prev = hist[-2] if len(hist) >= 2 else 0.0
        atr = talib.ATR(high, low, close, timeperiod=14)[-1]
    else:
        s = pd.Series(close)
        ema20 = float(s.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(s.ewm(span=50, adjust=False).mean().iloc[-1])
        ema200 = float(s.ewm(span=200, adjust=False).mean().iloc[-1])
        delta = s.diff()
        gain = delta.clip(lower=0).ewm(span=14, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(span=14, adjust=False).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi = float(100 - (100 / (1 + rs)).iloc[-1])
        ema12 = s.ewm(span=12, adjust=False).mean()
        ema26 = s.ewm(span=26, adjust=False).mean()
        macd_line_s = ema12 - ema26
        signal_s = macd_line_s.ewm(span=9, adjust=False).mean()
        hist_s = macd_line_s - signal_s
        macd_hist = float(hist_s.iloc[-1])
        macd_hist_prev = float(hist_s.iloc[-2]) if len(hist_s) >= 2 else 0.0
        atr = float(_atr_fallback(df).iloc[-1])

    vwap_series = _compute_vwap(df)
    vwap = float(vwap_series.iloc[-1])
    current_close = float(close[-1])
    supertrend_bull = bool(_supertrend(df).iloc[-1])

    avg_volume = np.mean(volume[-21:-1]) if len(volume) > 21 else np.mean(volume)
    vol_ratio = volume[-1] / avg_volume if avg_volume > 0 else 1.0

    return dict(
        close=current_close,
        ema20=ema20, ema50=ema50, ema200=ema200,
        rsi=rsi, macd_hist=macd_hist, macd_hist_prev=macd_hist_prev,
        atr=atr, vwap=vwap, supertrend_bull=supertrend_bull,
        vol_ratio=vol_ratio,
    )


# ---------------------------------------------------------------------------
# Confluence scoring
# ---------------------------------------------------------------------------

def score_bullish_confluence(df: pd.DataFrame) -> ConfluenceResult:
    """Score bullish technical setup. Returns 0–100 with per-indicator breakdown."""
    if df is None or len(df) < 50:
        return _empty_result("NEUTRAL")

    ind = _compute_indicators(df)
    c = ind["close"]

    signals = {
        "above_vwap": c > ind["vwap"],
        "ema20_above_ema50": ind["ema20"] > ind["ema50"],
        "ema50_above_ema200": ind["ema50"] > ind["ema200"],
        "rsi_in_range": 55 <= ind["rsi"] <= 75,
        "macd_hist_positive": ind["macd_hist"] > 0,
        "macd_hist_expanding": ind["macd_hist"] > ind["macd_hist_prev"],
        "supertrend_bullish": ind["supertrend_bull"],
        "volume_expansion": ind["vol_ratio"] >= 1.5,
    }

    weights = {
        "above_vwap": 20,
        "ema20_above_ema50": 15,
        "ema50_above_ema200": 10,
        "rsi_in_range": 15,
        "macd_hist_positive": 15,
        "macd_hist_expanding": 10,
        "supertrend_bullish": 10,
        "volume_expansion": 5,
    }

    score = sum(weights[k] for k, v in signals.items() if v)

    breakdown = {**signals, "rsi_value": ind["rsi"], "vol_ratio": ind["vol_ratio"]}
    direction = "BULLISH" if score >= 60 else "NEUTRAL"

    return ConfluenceResult(
        score=float(score), direction=direction, breakdown=breakdown,
        vwap=ind["vwap"], ema20=ind["ema20"], ema50=ind["ema50"], ema200=ind["ema200"],
        rsi=ind["rsi"], macd_hist=ind["macd_hist"], atr=ind["atr"],
        supertrend_bullish=ind["supertrend_bull"],
    )


def score_bearish_confluence(df: pd.DataFrame) -> ConfluenceResult:
    """Score bearish technical setup. Returns 0–100 with per-indicator breakdown."""
    if df is None or len(df) < 50:
        return _empty_result("NEUTRAL")

    ind = _compute_indicators(df)
    c = ind["close"]

    signals = {
        "below_vwap": c < ind["vwap"],
        "ema20_below_ema50": ind["ema20"] < ind["ema50"],
        "ema50_below_ema200": ind["ema50"] < ind["ema200"],
        "rsi_weak": ind["rsi"] < 45,
        "macd_hist_negative": ind["macd_hist"] < 0,
        "macd_hist_expanding_neg": ind["macd_hist"] < ind["macd_hist_prev"],
        "supertrend_bearish": not ind["supertrend_bull"],
        "volume_expansion": ind["vol_ratio"] >= 1.5,
    }

    weights = {
        "below_vwap": 20,
        "ema20_below_ema50": 15,
        "ema50_below_ema200": 10,
        "rsi_weak": 15,
        "macd_hist_negative": 15,
        "macd_hist_expanding_neg": 10,
        "supertrend_bearish": 10,
        "volume_expansion": 5,
    }

    score = sum(weights[k] for k, v in signals.items() if v)

    breakdown = {**signals, "rsi_value": ind["rsi"], "vol_ratio": ind["vol_ratio"]}
    direction = "BEARISH" if score >= 60 else "NEUTRAL"

    return ConfluenceResult(
        score=float(score), direction=direction, breakdown=breakdown,
        vwap=ind["vwap"], ema20=ind["ema20"], ema50=ind["ema50"], ema200=ind["ema200"],
        rsi=ind["rsi"], macd_hist=ind["macd_hist"], atr=ind["atr"],
        supertrend_bullish=ind["supertrend_bull"],
    )


def _empty_result(direction: str) -> ConfluenceResult:
    return ConfluenceResult(
        score=0.0, direction=direction, breakdown={},
        vwap=0.0, ema20=0.0, ema50=0.0, ema200=0.0,
        rsi=50.0, macd_hist=0.0, atr=0.0, supertrend_bullish=False,
    )
