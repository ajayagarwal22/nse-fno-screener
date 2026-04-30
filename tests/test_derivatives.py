"""Tests for Layer 4 — Derivatives Intelligence Engine."""
import pytest
import pandas as pd
from unittest.mock import patch

from app.engines.derivatives import (
    analyze_derivatives, _classify_pcr, _compute_max_pain,
    _detect_writing_bias, _classify_oi_interpretation,
    PCRSignal, OIInterpretation, WritingBias,
)


def _make_option_chain(spot: float = 22000.0) -> pd.DataFrame:
    """Synthetic option chain around a spot price."""
    strikes = [s for s in range(int(spot) - 500, int(spot) + 600, 100)]
    rows = []
    for strike in strikes:
        # CE: OI heavy above spot (call writing)
        ce_oi = max(1000, int((strike - spot + 500) * 80)) if strike >= spot else 500
        # PE: OI heavy below spot (put writing)
        pe_oi = max(1000, int((spot - strike + 500) * 80)) if strike <= spot else 500

        for opt_type, oi in [("CE", ce_oi), ("PE", pe_oi)]:
            ltp = max(0.5, abs(spot - strike) * 0.1 + 50)
            rows.append({
                "strike": float(strike),
                "type": opt_type,
                "oi": oi,
                "volume": oi // 2,
                "iv": 15.0 + (0.5 if opt_type == "PE" else 0.0),
                "ltp": ltp,
                "bid": ltp - 1,
                "ask": ltp + 1,
                "tradingsymbol": f"NIFTY{strike}{opt_type}",
            })
    return pd.DataFrame(rows)


class TestDerivatives:
    def test_pcr_classification_bearish(self):
        assert _classify_pcr(0.5) == PCRSignal.BEARISH

    def test_pcr_classification_neutral(self):
        assert _classify_pcr(1.0) == PCRSignal.NEUTRAL

    def test_pcr_classification_bullish(self):
        assert _classify_pcr(1.35) == PCRSignal.BULLISH

    def test_pcr_classification_overheated(self):
        assert _classify_pcr(1.6) == PCRSignal.OVERHEATED

    def test_max_pain_within_strike_range(self):
        chain = _make_option_chain(22000)
        max_pain = _compute_max_pain(chain)
        assert 21000 <= max_pain <= 23000

    def test_oi_interpretation_long_buildup(self):
        result = _classify_oi_interpretation(price_change_pct=0.5, oi_change_pct=10.0)
        assert result == OIInterpretation.LONG_BUILDUP

    def test_oi_interpretation_short_buildup(self):
        result = _classify_oi_interpretation(price_change_pct=-0.5, oi_change_pct=10.0)
        assert result == OIInterpretation.SHORT_BUILDUP

    def test_oi_interpretation_short_covering(self):
        result = _classify_oi_interpretation(price_change_pct=0.5, oi_change_pct=-10.0)
        assert result == OIInterpretation.SHORT_COVERING

    def test_oi_interpretation_long_unwinding(self):
        result = _classify_oi_interpretation(price_change_pct=-0.5, oi_change_pct=-10.0)
        assert result == OIInterpretation.LONG_UNWINDING

    def test_writing_bias_call_writing(self):
        chain = _make_option_chain(22000)
        # Artificially inflate CE OI near spot to simulate call writing
        chain.loc[(chain["type"] == "CE") & (chain["strike"].between(22000, 22200)), "oi"] = 50000
        bias = _detect_writing_bias(chain, spot=22000)
        assert bias == WritingBias.CALL_WRITING

    def test_analyze_derivatives_returns_valid_signal(self):
        chain = _make_option_chain(22000)
        with patch("app.engines.derivatives.kite_client") as mock_kite:
            mock_kite.get_option_chain.return_value = chain
            signal = analyze_derivatives("NIFTY", spot=22000, price_change_pct=0.3, oi_change_pct=5.0)
        assert signal.pcr > 0
        assert signal.max_pain > 0
        assert signal.oi_interpretation == OIInterpretation.LONG_BUILDUP
        assert isinstance(signal.summary, str) and len(signal.summary) > 0

    def test_empty_chain_returns_neutral_signal(self):
        with patch("app.engines.derivatives.kite_client") as mock_kite:
            mock_kite.get_option_chain.return_value = pd.DataFrame()
            signal = analyze_derivatives("UNKNOWN", spot=100.0)
        assert signal.pcr == 1.0
        assert signal.supports_call_buy is False
        assert signal.supports_put_buy is False
