"""Tests for Layer 3 — Technical Confluence Engine."""
import pytest
import pandas as pd
import numpy as np

from app.engines.technical import score_bullish_confluence, score_bearish_confluence, _compute_vwap


def _make_bullish_df(n: int = 120) -> pd.DataFrame:
    """Synthetic strongly bullish OHLCV with rising EMAs, RSI in 55-70, expanding volume."""
    np.random.seed(0)
    base = 20000.0
    closes = [base + i * 12 + np.random.randn() * 8 for i in range(n)]
    df = pd.DataFrame({
        "open": [c - 3 for c in closes],
        "high": [c + 20 for c in closes],
        "low": [c - 10 for c in closes],
        "close": closes,
        "volume": [500_000 + i * 3000 + np.random.randint(0, 50_000) for i in range(n)],
    })
    df.index = pd.date_range("2024-01-02 09:15", periods=n, freq="5min")
    return df


def _make_bearish_df(n: int = 120) -> pd.DataFrame:
    """Synthetic strongly bearish OHLCV."""
    np.random.seed(1)
    base = 22000.0
    closes = [base - i * 12 + np.random.randn() * 8 for i in range(n)]
    df = pd.DataFrame({
        "open": [c + 3 for c in closes],
        "high": [c + 10 for c in closes],
        "low": [c - 20 for c in closes],
        "close": closes,
        "volume": [500_000 + i * 2000 + np.random.randint(0, 50_000) for i in range(n)],
    })
    df.index = pd.date_range("2024-01-02 09:15", periods=n, freq="5min")
    return df


class TestTechnicalConfluence:
    def test_bullish_confluence_score_positive(self):
        df = _make_bullish_df()
        result = score_bullish_confluence(df)
        assert result.score > 0
        assert result.rsi > 0
        assert result.vwap > 0

    def test_bearish_confluence_score_positive(self):
        df = _make_bearish_df()
        result = score_bearish_confluence(df)
        assert result.score > 0
        assert result.rsi > 0

    def test_bullish_direction_on_uptrend(self):
        df = _make_bullish_df(n=200)
        result = score_bullish_confluence(df)
        # Strongly trending up data should score well
        assert result.score >= 30  # At minimum some signals should fire

    def test_bearish_scores_higher_on_downtrend(self):
        df = _make_bearish_df(n=200)
        bear = score_bearish_confluence(df)
        bull = score_bullish_confluence(df)
        assert bear.score > bull.score

    def test_insufficient_data_returns_zero_score(self):
        df = _make_bullish_df(n=10)
        result = score_bullish_confluence(df)
        assert result.score == 0.0
        assert result.direction == "NEUTRAL"

    def test_vwap_computation(self):
        df = _make_bullish_df(n=50)
        vwap = _compute_vwap(df)
        assert vwap.notna().all()
        # VWAP should be between low and high
        assert float(vwap.iloc[-1]) > df["low"].min()
        assert float(vwap.iloc[-1]) < df["high"].max()

    def test_breakdown_dict_has_required_keys(self):
        df = _make_bullish_df()
        result = score_bullish_confluence(df)
        required = {"above_vwap", "ema20_above_ema50", "rsi_in_range", "macd_hist_positive"}
        assert required.issubset(set(result.breakdown.keys()))

    def test_atr_is_positive(self):
        df = _make_bullish_df()
        result = score_bullish_confluence(df)
        assert result.atr > 0
