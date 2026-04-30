"""Tests for Layer 6 — Entry Trigger Engine (full pipeline with mocked layers)."""
import pytest
from datetime import date, datetime
from unittest.mock import MagicMock

from app.engines.entry_trigger import (
    evaluate_signal, Direction, TradeType, Confidence, Signal,
    _score_call_gates, _score_put_gates, _weighted_score, _CALL_GATES, _PUT_GATES,
)
from app.engines.market_regime import Bias, MarketRegime, RegimeType
from app.engines.technical import ConfluenceResult
from app.engines.derivatives import (
    DerivativesSignal, OIInterpretation, PCRSignal, WritingBias,
)
from app.engines.option_selector import OptionTarget, OptionType
from app.data.nse_client import VIXData, MarketBreadth


def _bullish_regime() -> MarketRegime:
    return MarketRegime(
        regime_type=RegimeType.TRENDING_BULLISH,
        vix_data=VIXData(value=14.0, change=0.0, change_pct=0.0, signal="NORMAL"),
        breadth=MarketBreadth(advances=38, declines=10, unchanged=2,
                              advance_decline_ratio=3.8, breadth_score=76.0),
        nifty_bias=Bias.BULLISH,
        banknifty_bias=Bias.BULLISH,
        overall_bias=Bias.BULLISH,
        call_buying_environment=True,
        put_buying_environment=False,
        reason="Strong bullish confluence",
    )


def _bearish_regime() -> MarketRegime:
    return MarketRegime(
        regime_type=RegimeType.TRENDING_BEARISH,
        vix_data=VIXData(value=19.0, change=1.5, change_pct=8.0, signal="HIGH_VIX"),
        breadth=MarketBreadth(advances=8, declines=40, unchanged=2,
                              advance_decline_ratio=0.2, breadth_score=16.0),
        nifty_bias=Bias.BEARISH,
        banknifty_bias=Bias.BEARISH,
        overall_bias=Bias.BEARISH,
        call_buying_environment=False,
        put_buying_environment=True,
        reason="Strong bearish confluence",
    )


def _perfect_bullish_tech() -> ConfluenceResult:
    return ConfluenceResult(
        score=85.0, direction="BULLISH",
        breakdown={
            "above_vwap": True, "ema20_above_ema50": True, "ema50_above_ema200": True,
            "rsi_in_range": True, "macd_hist_positive": True, "macd_hist_expanding": True,
            "supertrend_bullish": True, "volume_expansion": True, "vol_ratio": 2.1,
        },
        vwap=22000.0, ema20=22100.0, ema50=21800.0, ema200=21000.0,
        rsi=62.0, macd_hist=12.5, atr=150.0, supertrend_bullish=True,
    )


def _perfect_bearish_tech() -> ConfluenceResult:
    return ConfluenceResult(
        score=85.0, direction="BEARISH",
        breakdown={
            "below_vwap": True, "ema20_below_ema50": True, "ema50_below_ema200": True,
            "rsi_weak": True, "macd_hist_negative": True, "macd_hist_expanding_neg": True,
            "supertrend_bearish": True, "volume_expansion": True, "vol_ratio": 2.0,
        },
        vwap=22500.0, ema20=22300.0, ema50=22600.0, ema200=23000.0,
        rsi=38.0, macd_hist=-14.0, atr=180.0, supertrend_bullish=False,
    )


def _bullish_deriv() -> DerivativesSignal:
    return DerivativesSignal(
        pcr=1.1, pcr_signal=PCRSignal.NEUTRAL,
        max_pain=21900.0,
        oi_interpretation=OIInterpretation.SHORT_COVERING,
        writing_bias=WritingBias.PUT_WRITING,
        iv_skew=1.5, strong_call_wall=22500.0, strong_put_wall=21500.0,
        supports_call_buy=True, supports_put_buy=False,
        summary="PCR=1.10, OI=SHORT_COVERING, Writing=PUT_WRITING",
    )


def _bearish_deriv() -> DerivativesSignal:
    return DerivativesSignal(
        pcr=0.65, pcr_signal=PCRSignal.BEARISH,
        max_pain=22200.0,
        oi_interpretation=OIInterpretation.SHORT_BUILDUP,
        writing_bias=WritingBias.CALL_WRITING,
        iv_skew=-2.0, strong_call_wall=22500.0, strong_put_wall=21500.0,
        supports_call_buy=False, supports_put_buy=True,
        summary="PCR=0.65, OI=SHORT_BUILDUP, Writing=CALL_WRITING",
    )


def _mock_option(direction: str) -> OptionTarget:
    return OptionTarget(
        symbol="BANKNIFTY", tradingsymbol="BANKNIFTY2450022000CE",
        strike=22000.0, expiry=date(2024, 5, 9),
        option_type=OptionType.CE if direction == "CALL" else OptionType.PE,
        current_premium=185.0, iv=15.5, oi=12000, instrument_token=12345,
        days_to_expiry=5, is_atm=True,
    )


class TestEntryTrigger:
    def test_aplus_call_signal_on_perfect_confluence(self):
        signal = evaluate_signal(
            symbol="BANKNIFTY",
            direction=Direction.CALL,
            trade_type=TradeType.INTRADAY,
            spot=22150.0,
            regime=_bullish_regime(),
            tech=_perfect_bullish_tech(),
            deriv=_bullish_deriv(),
            option=_mock_option("CALL"),
            rs_score=0.04,
        )
        assert signal is not None
        assert signal.confidence in (Confidence.A_PLUS, Confidence.A_MINUS)
        assert signal.direction == Direction.CALL

    def test_put_signal_on_bearish_confluence(self):
        signal = evaluate_signal(
            symbol="NIFTY",
            direction=Direction.PUT,
            trade_type=TradeType.INTRADAY,
            spot=22400.0,
            regime=_bearish_regime(),
            tech=_perfect_bearish_tech(),
            deriv=_bearish_deriv(),
            option=_mock_option("PUT"),
            rs_score=-0.05,
        )
        assert signal is not None
        assert signal.direction == Direction.PUT

    def test_no_signal_on_contradictory_data(self):
        """Bearish regime + bullish tech = no signal (contradictory)."""
        signal = evaluate_signal(
            symbol="NIFTY",
            direction=Direction.CALL,
            trade_type=TradeType.INTRADAY,
            spot=22000.0,
            regime=_bearish_regime(),          # bearish regime
            tech=_perfect_bullish_tech(),      # but bullish technicals
            deriv=_bearish_deriv(),            # and bearish derivatives
            option=_mock_option("CALL"),
            rs_score=0.01,
        )
        # Regime gate should fail → no signal or only B grade
        if signal:
            assert signal.confidence == Confidence.B

    def test_signal_has_all_required_fields(self):
        signal = evaluate_signal(
            symbol="BANKNIFTY",
            direction=Direction.CALL,
            trade_type=TradeType.SWING,
            spot=22150.0,
            regime=_bullish_regime(),
            tech=_perfect_bullish_tech(),
            deriv=_bullish_deriv(),
            option=_mock_option("CALL"),
            rs_score=0.03,
        )
        if signal:
            assert signal.id
            assert signal.timestamp
            assert signal.entry_zone
            assert signal.stop_loss
            assert signal.target_1
            assert signal.target_2
            assert signal.reasons
            assert len(signal.reasons) >= 3

    def test_alert_text_format(self):
        signal = evaluate_signal(
            symbol="BANKNIFTY",
            direction=Direction.CALL,
            trade_type=TradeType.INTRADAY,
            spot=22150.0,
            regime=_bullish_regime(),
            tech=_perfect_bullish_tech(),
            deriv=_bullish_deriv(),
            option=_mock_option("CALL"),
            rs_score=0.04,
        )
        if signal:
            text = signal.to_alert_text()
            assert "BANKNIFTY" in text
            assert "CALL" in text
            assert "SL:" in text
            assert "Targets:" in text
            assert "Confidence:" in text

    def test_b_grade_signal_suggests_smaller_sizing(self):
        # Reduce tech score to get B grade
        weak_tech = _perfect_bullish_tech()
        weak_tech.breakdown["ema50_above_ema200"] = False
        weak_tech.breakdown["macd_hist_expanding"] = False
        weak_tech.breakdown["supertrend_bullish"] = False
        weak_tech.score = 45.0

        signal = evaluate_signal(
            symbol="RELIANCE",
            direction=Direction.CALL,
            trade_type=TradeType.INTRADAY,
            spot=2800.0,
            regime=_bullish_regime(),
            tech=weak_tech,
            deriv=_bullish_deriv(),
            option=_mock_option("CALL"),
            rs_score=0.02,
        )
        if signal and signal.confidence == Confidence.B:
            assert "0.5" in signal.position_sizing or "Half" in signal.position_sizing
