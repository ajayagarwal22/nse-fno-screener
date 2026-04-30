"""Layer 6 — Entry Trigger Engine.

Aggregates outputs from Layers 1–5 and emits a Signal only when all gates pass.
Implements strict confluence gating with A+/A-/B confidence grading.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
import uuid

from app.engines.derivatives import DerivativesSignal, OIInterpretation, WritingBias
from app.engines.market_regime import Bias, MarketRegime, RegimeType
from app.engines.option_selector import OptionTarget, OptionType, TradeType
from app.engines.technical import ConfluenceResult


class Direction(str, Enum):
    CALL = "CALL"
    PUT = "PUT"


class Confidence(str, Enum):
    A_PLUS = "A+"
    A_MINUS = "A-"
    B = "B"


_CONFIDENCE_MIN_ALERT = {"A+", "A-", "B"}

# Weights for each gate (total = 100)
_CALL_GATES = [
    ("regime_supportive", 15),
    ("rs_positive", 10),
    ("above_vwap", 15),
    ("ema_bullish", 15),
    ("rsi_bullish", 10),
    ("macd_bullish", 10),
    ("put_writing_or_short_covering", 10),
    ("pcr_supportive", 10),
    ("volume_breakout", 5),
]

_PUT_GATES = [
    ("regime_supportive", 15),
    ("rs_negative", 10),
    ("below_vwap", 15),
    ("ema_bearish", 15),
    ("rsi_bearish", 10),
    ("macd_bearish", 10),
    ("call_writing_or_short_buildup", 10),
    ("pcr_bearish", 10),
    ("volume_breakout", 5),
]


@dataclass
class Signal:
    id: str
    timestamp: datetime
    symbol: str
    direction: Direction
    trade_type: TradeType
    confidence: Confidence
    gates_passed: dict[str, bool]
    gate_score: float

    # From option selector (Layer 5)
    option: Optional[OptionTarget]

    # From technical layer
    entry_zone: str
    stop_loss: str
    target_1: str
    target_2: str
    rr_ratio: str
    position_sizing: str
    time_sensitivity: str

    # Narrative
    reasons: list[str]
    regime_type: str
    vix_level: float
    vix_signal: str
    rsi_value: float
    macd_status: str
    vwap_status: str
    oi_interpretation: str
    pcr_value: float
    pcr_signal: str
    iv_status: str

    def to_alert_text(self) -> str:
        opt_line = ""
        if self.option:
            opt_line = (
                f"Trade: Buy {self.option.strike:.0f} {self.option.option_type.value} "
                f"{self.option.expiry.strftime('%d %b %Y')} "
                f"(DTE={self.option.days_to_expiry}, Premium={self.option.current_premium:.2f})"
            )
        reason_block = "\n".join(f"• {r}" for r in self.reasons)
        return (
            f"{'='*48}\n"
            f"{self.symbol} {self.direction.value} MOMENTUM SETUP\n"
            f"{'='*48}\n"
            f"Bias: {self.direction.value} {self.trade_type.value}\n\n"
            f"Reason:\n{reason_block}\n\n"
            f"{opt_line}\n"
            f"Entry: {self.entry_zone}\n"
            f"SL: {self.stop_loss}\n"
            f"Targets: {self.target_1} | {self.target_2}\n"
            f"R:R: {self.rr_ratio}\n"
            f"Confidence: {self.confidence.value}\n"
            f"Position Size: {self.position_sizing}\n"
            f"Time Sensitivity: {self.time_sensitivity}\n"
            f"{'='*48}"
        )


def _score_call_gates(
    regime: MarketRegime,
    tech: ConfluenceResult,
    deriv: DerivativesSignal,
    rs_score: float,
) -> dict[str, bool]:
    return {
        "regime_supportive": regime.regime_type not in (
            RegimeType.TRENDING_BEARISH, RegimeType.EVENT_RISK, RegimeType.HIGH_VOL_EXPANSION
        ) and regime.overall_bias != Bias.BEARISH,
        "rs_positive": rs_score > 0.01,
        "above_vwap": tech.breakdown.get("above_vwap", False),
        "ema_bullish": (
            tech.breakdown.get("ema20_above_ema50", False)
            and tech.breakdown.get("ema50_above_ema200", False)
        ),
        "rsi_bullish": 55 <= tech.rsi <= 75,
        "macd_bullish": tech.macd_hist > 0 and tech.breakdown.get("macd_hist_expanding", False),
        "put_writing_or_short_covering": deriv.writing_bias == WritingBias.PUT_WRITING or
            deriv.oi_interpretation == OIInterpretation.SHORT_COVERING,
        "pcr_supportive": deriv.pcr >= 0.8,
        "volume_breakout": tech.breakdown.get("volume_expansion", False),
    }


def _score_put_gates(
    regime: MarketRegime,
    tech: ConfluenceResult,
    deriv: DerivativesSignal,
    rs_score: float,
) -> dict[str, bool]:
    return {
        "regime_supportive": regime.regime_type not in (
            RegimeType.TRENDING_BULLISH, RegimeType.EVENT_RISK
        ) and regime.overall_bias != Bias.BULLISH,
        "rs_negative": rs_score < -0.01,
        "below_vwap": tech.breakdown.get("below_vwap", False),
        "ema_bearish": (
            tech.breakdown.get("ema20_below_ema50", False)
            and tech.breakdown.get("ema50_below_ema200", False)
        ),
        "rsi_bearish": tech.rsi < 45,
        "macd_bearish": tech.macd_hist < 0 and tech.breakdown.get("macd_hist_expanding_neg", False),
        "call_writing_or_short_buildup": deriv.writing_bias == WritingBias.CALL_WRITING or
            deriv.oi_interpretation == OIInterpretation.SHORT_BUILDUP,
        "pcr_bearish": deriv.pcr < 1.0,
        "volume_breakout": tech.breakdown.get("volume_expansion", False),
    }


def _weighted_score(gates: dict[str, bool], gate_defs: list[tuple[str, int]]) -> float:
    total_weight = sum(w for _, w in gate_defs)
    passed_weight = sum(w for name, w in gate_defs if gates.get(name, False))
    return (passed_weight / total_weight) * 100


def _grade_confidence(score: float, total_gates: int, passed_gates: int) -> Optional[Confidence]:
    if passed_gates == total_gates and score >= 95:
        return Confidence.A_PLUS
    elif passed_gates >= total_gates - 1 and score >= 75:
        return Confidence.A_MINUS
    elif passed_gates >= total_gates - 3 and score >= 55:
        return Confidence.B
    return None


def _build_reasons(
    direction: Direction,
    tech: ConfluenceResult,
    deriv: DerivativesSignal,
    regime: MarketRegime,
) -> list[str]:
    reasons = []
    if direction == Direction.CALL:
        if tech.breakdown.get("above_vwap"):
            reasons.append(f"Price above VWAP ({tech.vwap:.2f})")
        reasons.append(f"RSI {tech.rsi:.1f} — bullish range")
        if tech.macd_hist > 0:
            reasons.append(f"MACD positive histogram ({tech.macd_hist:+.2f})")
        reasons.append(f"OI: {deriv.oi_interpretation.value}")
        reasons.append(f"PCR {deriv.pcr:.2f} ({deriv.pcr_signal.value})")
        reasons.append(f"VIX {regime.vix_data.value:.1f} ({regime.vix_data.signal})")
    else:
        if tech.breakdown.get("below_vwap"):
            reasons.append(f"Price below VWAP ({tech.vwap:.2f})")
        reasons.append(f"RSI {tech.rsi:.1f} — weak")
        if tech.macd_hist < 0:
            reasons.append(f"MACD negative expansion ({tech.macd_hist:+.2f})")
        reasons.append(f"OI: {deriv.oi_interpretation.value}")
        reasons.append(f"Heavy CE writing at {deriv.strong_call_wall:.0f}")
        reasons.append(f"PCR {deriv.pcr:.2f} ({deriv.pcr_signal.value})")
        reasons.append(f"VIX {regime.vix_data.value:.1f} — rising" if regime.vix_data.change > 0 else f"VIX {regime.vix_data.value:.1f}")
    return reasons


def _compute_targets(spot: float, atr: float, direction: Direction) -> tuple[str, str, str, str, str]:
    """Compute entry, SL, T1, T2 and R:R based on spot structure and ATR."""
    if direction == Direction.CALL:
        entry = f"Premium breakout above {spot:.2f} zone"
        sl = f"Spot closes below VWAP or {spot - atr:.2f}"
        t1 = f"{spot + atr:.2f} (1:1 RR)"
        t2 = f"{spot + 2 * atr:.2f} (1:2 RR)"
    else:
        entry = f"Premium breakdown below {spot:.2f} zone"
        sl = f"Spot reclaims VWAP or {spot + atr:.2f}"
        t1 = f"{spot - atr:.2f} (1:1 RR)"
        t2 = f"{spot - 2 * atr:.2f} (1:2 RR)"
    return entry, sl, t1, t2, "1:2"


def evaluate_signal(
    symbol: str,
    direction: Direction,
    trade_type: TradeType,
    spot: float,
    regime: MarketRegime,
    tech: ConfluenceResult,
    deriv: DerivativesSignal,
    option: Optional[OptionTarget],
    rs_score: float = 0.0,
) -> Optional[Signal]:
    """
    Returns a Signal if confluence gates pass, else None.
    """
    if direction == Direction.CALL:
        gates = _score_call_gates(regime, tech, deriv, rs_score)
        gate_defs = _CALL_GATES
    else:
        gates = _score_put_gates(regime, tech, deriv, rs_score)
        gate_defs = _PUT_GATES

    score = _weighted_score(gates, gate_defs)
    passed = sum(1 for v in gates.values() if v)
    confidence = _grade_confidence(score, len(gate_defs), passed)

    if confidence is None:
        return None

    reasons = _build_reasons(direction, tech, deriv, regime)
    entry, sl, t1, t2, rr = _compute_targets(spot, max(tech.atr, spot * 0.005), direction)

    sizing = {
        Confidence.A_PLUS: "Full position (2–3% capital)",
        Confidence.A_MINUS: "Standard position (1.5–2% capital)",
        Confidence.B: "Half position (0.5–1% capital)",
    }[confidence]

    time_note = (
        "Avoid holding after 2:30 PM if momentum fades."
        if trade_type == TradeType.INTRADAY
        else "Review at end of day; trail stop after T1."
    )

    return Signal(
        id=str(uuid.uuid4()),
        timestamp=datetime.now(),
        symbol=symbol,
        direction=direction,
        trade_type=trade_type,
        confidence=confidence,
        gates_passed=gates,
        gate_score=round(score, 1),
        option=option,
        entry_zone=entry,
        stop_loss=sl,
        target_1=t1,
        target_2=t2,
        rr_ratio=rr,
        position_sizing=sizing,
        time_sensitivity=time_note,
        reasons=reasons,
        regime_type=regime.regime_type.value,
        vix_level=regime.vix_data.value,
        vix_signal=regime.vix_data.signal,
        rsi_value=tech.rsi,
        macd_status=f"hist={tech.macd_hist:+.3f}",
        vwap_status=f"{'Above' if tech.breakdown.get('above_vwap') else 'Below'} VWAP ({tech.vwap:.2f})",
        oi_interpretation=deriv.oi_interpretation.value,
        pcr_value=deriv.pcr,
        pcr_signal=deriv.pcr_signal.value,
        iv_status=f"IV skew={deriv.iv_skew:+.1f}%",
    )
