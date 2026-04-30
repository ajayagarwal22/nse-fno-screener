"""Tests for Layer 1 — Market Regime Engine."""
import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch

from app.engines.market_regime import (
    analyze_market_regime, RegimeType, Bias, MarketRegime
)
from app.data.nse_client import VIXData, MarketBreadth


def _make_ohlcv(n: int = 100, trend: str = "up") -> pd.DataFrame:
    """Generate synthetic OHLCV data with a defined trend."""
    np.random.seed(42)
    base = 20000.0
    closes = [base + i * (1 if trend == "up" else -1) * 10 + np.random.randn() * 20 for i in range(n)]
    df = pd.DataFrame({
        "open": [c - 5 for c in closes],
        "high": [c + 15 for c in closes],
        "low": [c - 15 for c in closes],
        "close": closes,
        "volume": [1_000_000 + np.random.randint(0, 500_000) for _ in range(n)],
    })
    df.index = pd.date_range("2024-01-01 09:15", periods=n, freq="5min")
    return df


@pytest.fixture
def mock_vix_low():
    return VIXData(value=11.5, change=-0.5, change_pct=-4.2, signal="LOW_VIX")


@pytest.fixture
def mock_vix_normal():
    return VIXData(value=15.0, change=0.1, change_pct=0.5, signal="NORMAL")


@pytest.fixture
def mock_vix_high():
    return VIXData(value=22.0, change=2.0, change_pct=10.0, signal="HIGH_VIX")


@pytest.fixture
def mock_breadth_bullish():
    return MarketBreadth(advances=38, declines=10, unchanged=2, advance_decline_ratio=3.8, breadth_score=76.0)


@pytest.fixture
def mock_breadth_bearish():
    return MarketBreadth(advances=8, declines=40, unchanged=2, advance_decline_ratio=0.2, breadth_score=16.0)


@pytest.fixture
def mock_breadth_neutral():
    return MarketBreadth(advances=25, declines=25, unchanged=0, advance_decline_ratio=1.0, breadth_score=50.0)


class TestMarketRegime:
    def test_trending_bullish_regime(self, mock_vix_normal, mock_breadth_bullish):
        nifty_df = _make_ohlcv(100, trend="up")
        bnf_df = _make_ohlcv(100, trend="up")
        with patch("app.engines.market_regime.fetch_vix", return_value=mock_vix_normal), \
             patch("app.engines.market_regime.fetch_market_breadth", return_value=mock_breadth_bullish):
            regime = analyze_market_regime(nifty_df, bnf_df)
        assert regime.regime_type == RegimeType.TRENDING_BULLISH
        assert regime.overall_bias == Bias.BULLISH
        assert regime.call_buying_environment is True

    def test_trending_bearish_regime(self, mock_vix_normal, mock_breadth_bearish):
        nifty_df = _make_ohlcv(100, trend="down")
        bnf_df = _make_ohlcv(100, trend="down")
        with patch("app.engines.market_regime.fetch_vix", return_value=mock_vix_normal), \
             patch("app.engines.market_regime.fetch_market_breadth", return_value=mock_breadth_bearish):
            regime = analyze_market_regime(nifty_df, bnf_df)
        assert regime.regime_type == RegimeType.TRENDING_BEARISH
        assert regime.put_buying_environment is True

    def test_high_vix_expansion(self, mock_vix_high, mock_breadth_neutral):
        with patch("app.engines.market_regime.fetch_vix", return_value=mock_vix_high), \
             patch("app.engines.market_regime.fetch_market_breadth", return_value=mock_breadth_neutral):
            regime = analyze_market_regime()
        assert regime.regime_type == RegimeType.HIGH_VOL_EXPANSION

    def test_low_vix_theta_decay(self, mock_vix_low, mock_breadth_neutral):
        with patch("app.engines.market_regime.fetch_vix", return_value=mock_vix_low), \
             patch("app.engines.market_regime.fetch_market_breadth", return_value=mock_breadth_neutral):
            regime = analyze_market_regime()
        assert regime.regime_type == RegimeType.THETA_DECAY
        # Theta decay env: option buying not favored
        assert regime.call_buying_environment is False

    def test_event_day_suppression(self, mock_vix_normal, mock_breadth_bullish):
        nifty_df = _make_ohlcv(100, trend="up")
        with patch("app.engines.market_regime.fetch_vix", return_value=mock_vix_normal), \
             patch("app.engines.market_regime.fetch_market_breadth", return_value=mock_breadth_bullish):
            regime = analyze_market_regime(nifty_df=nifty_df, is_event_day=True)
        assert regime.regime_type == RegimeType.EVENT_RISK

    def test_vix_classification_thresholds(self, mock_breadth_neutral):
        for vix_val, expected_signal in [(11.0, "LOW_VIX"), (15.0, "NORMAL"), (20.0, "HIGH_VIX"), (28.0, "EXTREME_VIX")]:
            vix = VIXData(value=vix_val, change=0, change_pct=0, signal=expected_signal)
            with patch("app.engines.market_regime.fetch_vix", return_value=vix), \
                 patch("app.engines.market_regime.fetch_market_breadth", return_value=mock_breadth_neutral):
                regime = analyze_market_regime()
            assert regime.vix_data.signal == expected_signal
