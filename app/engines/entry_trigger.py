"""Layer 6 — Entry Trigger Engine.

Aggregates outputs from Layers 1–5 and emits a Signal only when all gates pass.
Implements strict confluence gating with A+/A-/B confidence grading.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
import uuid

from app.engines.admin_cfg import cfg
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

# MTF gate weights (total = 100)
# rsi_divergence is the highest-weight factor — required for A+, boosts A-/B
_CALL_GATES = [
    ("regime_supportive",        10),
    ("rs_positive",               5),
    ("rsi_divergence",           30),   # Highest weight — MTF 15-min
    ("htf_trend_bullish",        15),   # Daily trend filter
    ("macd_bull_cross",          15),   # LTF 5-min confirmation
    ("above_vwap",               10),   # LTF 5-min confirmation
    ("volume_expansion",          5),   # LTF 5-min confirmation
    ("put_writing_or_short_covering", 5),
    ("pcr_supportive",            5),
]

_PUT_GATES = [
    ("regime_supportive",        10),
    ("rs_negative",               5),
    ("rsi_divergence",           30),   # Highest weight — MTF 15-min
    ("htf_trend_bearish",        15),   # Daily trend filter
    ("macd_bear_cross",          15),   # LTF 5-min confirmation
    ("below_vwap",               10),   # LTF 5-min confirmation
    ("volume_expansion",          5),   # LTF 5-min confirmation
    ("call_writing_or_short_buildup", 5),
    ("pcr_bearish",               5),
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

    # MTF divergence info
    divergence_detected: bool = False
    divergence_strength: float = 0.0
    htf_trend: str = "NEUTRAL"

    # Candlestick pattern detected on 15-min (empty string = none)
    candle_pattern: str = ""

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
        "rsi_divergence": tech.rsi_divergence,
        "htf_trend_bullish": tech.htf_trend == "BULLISH",
        "macd_bull_cross": tech.macd_cross,
        "above_vwap": tech.above_vwap,
        "volume_expansion": tech.volume_expansion,
        "put_writing_or_short_covering": deriv.writing_bias == WritingBias.PUT_WRITING or
            deriv.oi_interpretation == OIInterpretation.SHORT_COVERING,
        "pcr_supportive": deriv.pcr >= 0.8,
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
        "rsi_divergence": tech.rsi_divergence,
        "htf_trend_bearish": tech.htf_trend == "BEARISH",
        "macd_bear_cross": tech.macd_cross,
        "below_vwap": not tech.above_vwap,
        "volume_expansion": tech.volume_expansion,
        "call_writing_or_short_buildup": deriv.writing_bias == WritingBias.CALL_WRITING or
            deriv.oi_interpretation == OIInterpretation.SHORT_BUILDUP,
        "pcr_bearish": deriv.pcr < 1.0,
    }


def _weighted_score(gates: dict[str, bool], gate_defs: list[tuple[str, int]]) -> float:
    return float(sum(w for name, w in gate_defs if gates.get(name, False)))


def _grade_confidence(score: float, gates: dict[str, bool]) -> Optional[Confidence]:
    _a_plus  = cfg("layer6", "thresholds", "a_plus_score",              default=78)
    _a_minus = cfg("layer6", "thresholds", "a_minus_score",             default=70)
    _b       = cfg("layer6", "thresholds", "b_score",                   default=60)
    _div_req = cfg("layer6", "thresholds", "a_plus_requires_divergence", default=False)
    has_div  = gates.get("rsi_divergence", False)
    if score >= _a_plus and (not _div_req or has_div):
        return Confidence.A_PLUS
    elif score >= _a_minus:
        return Confidence.A_MINUS
    elif score >= _b:
        return Confidence.B
    return None


def _build_reasons(
    direction: Direction,
    tech: ConfluenceResult,
    deriv: DerivativesSignal,
    regime: MarketRegime,
) -> list[str]:
    reasons = []
    # RSI divergence is the primary reason — always listed first
    if tech.rsi_divergence:
        strength_pct = f"{tech.divergence_strength * 100:.0f}%"
        reasons.append(
            f"RSI {tech.divergence_type} Divergence on 15-min (strength={strength_pct})"
        )
    if tech.htf_trend != "NEUTRAL":
        ema_note = " — EMA stack aligned" if tech.htf_ema_aligned else ""
        reasons.append(f"Daily trend {tech.htf_trend}{ema_note}")

    if direction == Direction.CALL:
        if tech.above_vwap:
            reasons.append(f"Price above VWAP ({tech.vwap:.2f})")
        if tech.macd_cross:
            reasons.append(f"MACD histogram flipped bullish ({tech.macd_hist:+.2f})")
        if tech.volume_expansion:
            reasons.append("Volume expansion confirmed on 5-min")
        if tech.candle_pattern:
            reasons.append(f"Pattern: {tech.candle_pattern} on 15-min (+5 pts)")
        reasons.append(f"OI: {deriv.oi_interpretation.value}")
        reasons.append(f"PCR {deriv.pcr:.2f} ({deriv.pcr_signal.value})")
        reasons.append(f"VIX {regime.vix_data.value:.1f} ({regime.vix_data.signal})")
    else:
        if not tech.above_vwap:
            reasons.append(f"Price below VWAP ({tech.vwap:.2f})")
        if tech.macd_cross:
            reasons.append(f"MACD histogram flipped bearish ({tech.macd_hist:+.2f})")
        if tech.volume_expansion:
            reasons.append("Volume expansion confirmed on 5-min")
        if tech.candle_pattern:
            reasons.append(f"Pattern: {tech.candle_pattern} on 15-min (+5 pts)")
        reasons.append(f"OI: {deriv.oi_interpretation.value}")
        reasons.append(f"Heavy CE writing at {deriv.strong_call_wall:.0f}")
        reasons.append(f"PCR {deriv.pcr:.2f} ({deriv.pcr_signal.value})")
        reasons.append(f"VIX {regime.vix_data.value:.1f} — rising" if regime.vix_data.change > 0 else f"VIX {regime.vix_data.value:.1f}")
    return reasons


def _compute_targets(spot: float, atr: float, direction: Direction) -> tuple[str, str, str, str, str]:
    """Compute entry, SL, T1, T2 and R:R based on spot structure and ATR."""
    sl_m = cfg("layer6", "thresholds", "atr_sl_mult", default=0.7)
    t1_m = cfg("layer6", "thresholds", "atr_t1_mult", default=1.5)
    t2_m = cfg("layer6", "thresholds", "atr_t2_mult", default=2.5)
    # Entry zone is a small confirmation move (0.25× ATR) beyond current price
    # so WATCHING trades wait for a real breakout rather than entering immediately.
    confirm = round(atr * 0.25, 2)
    if direction == Direction.CALL:
        entry_level = round(spot + confirm, 2)
        entry = f"Premium breakout above {entry_level:.2f} zone"
        sl = f"Spot closes below VWAP or {spot - sl_m * atr:.2f}"
        t1 = f"{spot + t1_m * atr:.2f} (1:{t1_m:.0f} RR)"
        t2 = f"{spot + t2_m * atr:.2f} (1:{t2_m:.0f} RR)"
    else:
        entry_level = round(spot - confirm, 2)
        entry = f"Premium breakdown below {entry_level:.2f} zone"
        sl = f"Spot reclaims VWAP or {spot + sl_m * atr:.2f}"
        t1 = f"{spot - t1_m * atr:.2f} (1:{t1_m:.0f} RR)"
        t2 = f"{spot - t2_m * atr:.2f} (1:{t2_m:.0f} RR)"
    return entry, sl, t1, t2, f"1:{t2_m:.0f}"


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
    force_regime_ok: bool = False,
) -> Optional[Signal]:
    """
    Returns a Signal if confluence gates pass, else None.
    force_regime_ok: set True for index candidates whose regime alignment is
    already confirmed at candidacy level (nifty_bias / banknifty_bias).
    """
    if direction == Direction.CALL:
        gates = _score_call_gates(regime, tech, deriv, rs_score)
        gate_defs = _CALL_GATES
    else:
        gates = _score_put_gates(regime, tech, deriv, rs_score)
        gate_defs = _PUT_GATES

    if force_regime_ok:
        gates["regime_supportive"] = True

    score = _weighted_score(gates, gate_defs)
    confidence = _grade_confidence(score, gates)

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
        "Avoid holding after 3:15 PM if momentum fades."
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
        macd_status=f"{'Bullish cross' if tech.macd_cross and tech.macd_hist > 0 else 'Bearish cross' if tech.macd_cross else 'Flat'} (hist={tech.macd_hist:+.3f})",
        vwap_status=f"{'Above' if tech.above_vwap else 'Below'} VWAP ({tech.vwap:.2f})",
        oi_interpretation=deriv.oi_interpretation.value,
        pcr_value=deriv.pcr,
        pcr_signal=deriv.pcr_signal.value,
        iv_status=f"IV skew={deriv.iv_skew:+.1f}%",
        divergence_detected=tech.rsi_divergence,
        divergence_strength=tech.divergence_strength,
        htf_trend=tech.htf_trend,
        candle_pattern=tech.candle_pattern,
    )
