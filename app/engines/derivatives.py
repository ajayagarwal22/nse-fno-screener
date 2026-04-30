"""Layer 4 — Derivatives Intelligence Engine.

Interprets the option chain: PCR, Max Pain, OI buildup classification,
IV skew, and call/put writing concentration.
"""
from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd

from app.data.kite_client import kite_client


class OIInterpretation(str, Enum):
    LONG_BUILDUP = "LONG_BUILDUP"           # price↑ + OI↑
    SHORT_BUILDUP = "SHORT_BUILDUP"         # price↓ + OI↑
    SHORT_COVERING = "SHORT_COVERING"       # price↑ + OI↓
    LONG_UNWINDING = "LONG_UNWINDING"       # price↓ + OI↓
    NEUTRAL = "NEUTRAL"


class WritingBias(str, Enum):
    PUT_WRITING = "PUT_WRITING"     # bullish — institutions selling puts
    CALL_WRITING = "CALL_WRITING"   # bearish — institutions selling calls
    NEUTRAL = "NEUTRAL"


class PCRSignal(str, Enum):
    BEARISH = "BEARISH"      # PCR < 0.7
    NEUTRAL = "NEUTRAL"      # 0.7–1.3
    BULLISH = "BULLISH"      # PCR > 1.3 (but watch for overheated)
    OVERHEATED = "OVERHEATED"


@dataclass
class DerivativesSignal:
    pcr: float
    pcr_signal: PCRSignal
    max_pain: float
    oi_interpretation: OIInterpretation
    writing_bias: WritingBias
    iv_skew: float            # PE IV - CE IV at ATM; positive = put skew (bearish fear)
    strong_call_wall: float   # strike with highest CE OI (resistance)
    strong_put_wall: float    # strike with highest PE OI (support)
    supports_call_buy: bool
    supports_put_buy: bool
    summary: str


def _classify_pcr(pcr: float) -> PCRSignal:
    if pcr < 0.7:
        return PCRSignal.BEARISH
    elif pcr > 1.5:
        return PCRSignal.OVERHEATED
    elif pcr > 1.3:
        return PCRSignal.BULLISH
    return PCRSignal.NEUTRAL


def _compute_max_pain(chain: pd.DataFrame) -> float:
    """Find the strike where total OI-weighted loss for option writers is minimised."""
    strikes = chain["strike"].unique()
    min_loss = float("inf")
    max_pain_strike = 0.0

    ce_chain = chain[chain["type"] == "CE"][["strike", "oi"]].set_index("strike")
    pe_chain = chain[chain["type"] == "PE"][["strike", "oi"]].set_index("strike")

    for s in strikes:
        # loss to call writers if spot = s
        ce_loss = sum(
            row["oi"] * max(0, s - strike)
            for strike, row in ce_chain.iterrows()
        )
        # loss to put writers if spot = s
        pe_loss = sum(
            row["oi"] * max(0, strike - s)
            for strike, row in pe_chain.iterrows()
        )
        total = ce_loss + pe_loss
        if total < min_loss:
            min_loss = total
            max_pain_strike = float(s)

    return max_pain_strike


def _detect_writing_bias(chain: pd.DataFrame, spot: float) -> WritingBias:
    """
    Identify whether call writing or put writing dominates near ATM.
    High CE OI build at strikes above spot = call writing (bearish for spot).
    High PE OI build at strikes below spot = put writing (bullish for spot).
    """
    atm_range = spot * 0.02
    near_ce = chain[(chain["type"] == "CE") & (chain["strike"].between(spot, spot + atm_range))]
    near_pe = chain[(chain["type"] == "PE") & (chain["strike"].between(spot - atm_range, spot))]

    ce_oi = near_ce["oi"].sum()
    pe_oi = near_pe["oi"].sum()

    if ce_oi > pe_oi * 1.5:
        return WritingBias.CALL_WRITING
    elif pe_oi > ce_oi * 1.5:
        return WritingBias.PUT_WRITING
    return WritingBias.NEUTRAL


def _classify_oi_interpretation(price_change_pct: float, oi_change_pct: float) -> OIInterpretation:
    price_up = price_change_pct > 0.1
    price_dn = price_change_pct < -0.1
    oi_up = oi_change_pct > 0
    oi_dn = oi_change_pct < 0

    if price_up and oi_up:
        return OIInterpretation.LONG_BUILDUP
    elif price_dn and oi_up:
        return OIInterpretation.SHORT_BUILDUP
    elif price_up and oi_dn:
        return OIInterpretation.SHORT_COVERING
    elif price_dn and oi_dn:
        return OIInterpretation.LONG_UNWINDING
    return OIInterpretation.NEUTRAL


def analyze_derivatives(
    symbol: str,
    spot: float,
    price_change_pct: float = 0.0,
    oi_change_pct: float = 0.0,
) -> DerivativesSignal:
    """Main entry for Layer 4. Returns a structured derivatives signal."""
    chain = kite_client.get_option_chain(symbol)

    if chain.empty:
        return _empty_signal(spot)

    total_ce_oi = chain[chain["type"] == "CE"]["oi"].sum()
    total_pe_oi = chain[chain["type"] == "PE"]["oi"].sum()
    pcr = total_pe_oi / max(total_ce_oi, 1)
    pcr_signal = _classify_pcr(pcr)

    max_pain = _compute_max_pain(chain)

    oi_interp = _classify_oi_interpretation(price_change_pct, oi_change_pct)
    writing_bias = _detect_writing_bias(chain, spot)

    # IV skew: compare PE vs CE IV at strikes nearest ATM
    atm_ce = chain[chain["type"] == "CE"].iloc[(chain[chain["type"] == "CE"]["strike"] - spot).abs().argsort()[:1]]
    atm_pe = chain[chain["type"] == "PE"].iloc[(chain[chain["type"] == "PE"]["strike"] - spot).abs().argsort()[:1]]
    ce_iv = float(atm_ce["iv"].iloc[0]) if not atm_ce.empty else 0.0
    pe_iv = float(atm_pe["iv"].iloc[0]) if not atm_pe.empty else 0.0
    iv_skew = pe_iv - ce_iv

    call_wall_row = chain[chain["type"] == "CE"].nlargest(1, "oi")
    put_wall_row = chain[chain["type"] == "PE"].nlargest(1, "oi")
    call_wall = float(call_wall_row["strike"].iloc[0]) if not call_wall_row.empty else 0.0
    put_wall = float(put_wall_row["strike"].iloc[0]) if not put_wall_row.empty else 0.0

    supports_call = (
        oi_interp in (OIInterpretation.LONG_BUILDUP, OIInterpretation.SHORT_COVERING)
        and writing_bias == WritingBias.PUT_WRITING
        and pcr_signal in (PCRSignal.NEUTRAL, PCRSignal.BULLISH)
    )
    supports_put = (
        oi_interp in (OIInterpretation.SHORT_BUILDUP, OIInterpretation.LONG_UNWINDING)
        and writing_bias == WritingBias.CALL_WRITING
        and pcr_signal in (PCRSignal.NEUTRAL, PCRSignal.BEARISH)
    )

    summary = (
        f"PCR={pcr:.2f} ({pcr_signal.value}), OI={oi_interp.value}, "
        f"Writing={writing_bias.value}, MaxPain={max_pain:.0f}, IVSkew={iv_skew:+.1f}%"
    )

    return DerivativesSignal(
        pcr=round(pcr, 2),
        pcr_signal=pcr_signal,
        max_pain=max_pain,
        oi_interpretation=oi_interp,
        writing_bias=writing_bias,
        iv_skew=round(iv_skew, 2),
        strong_call_wall=call_wall,
        strong_put_wall=put_wall,
        supports_call_buy=supports_call,
        supports_put_buy=supports_put,
        summary=summary,
    )


def _empty_signal(spot: float) -> DerivativesSignal:
    return DerivativesSignal(
        pcr=1.0, pcr_signal=PCRSignal.NEUTRAL, max_pain=spot,
        oi_interpretation=OIInterpretation.NEUTRAL,
        writing_bias=WritingBias.NEUTRAL,
        iv_skew=0.0, strong_call_wall=0.0, strong_put_wall=0.0,
        supports_call_buy=False, supports_put_buy=False,
        summary="No option chain data available",
    )
