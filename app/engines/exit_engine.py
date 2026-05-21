"""Layer 7 — Exit Engine.

Computes exit parameters at signal creation and evaluates live exit conditions.
All stops are spot-price-structure based, never option-premium based.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from app.engines.admin_cfg import cfg
from app.engines.entry_trigger import Direction, Signal
from app.engines.technical import ConfluenceResult


class ExitReason(str, Enum):
    TARGET_1_HIT = "TARGET_1_HIT"
    TARGET_2_HIT = "TARGET_2_HIT"
    STOP_LOSS_HIT = "STOP_LOSS_HIT"
    RSI_DIVERGENCE = "RSI_DIVERGENCE"
    OI_REVERSAL = "OI_REVERSAL"
    VWAP_LOSS = "VWAP_LOSS"
    VOLUME_EXHAUSTION = "VOLUME_EXHAUSTION"
    TIME_EXIT = "TIME_EXIT"
    TRAILING_STOP = "TRAILING_STOP"


@dataclass
class ExitDecision:
    should_exit: bool
    reason: Optional[ExitReason]
    exit_type: str          # "FULL" | "PARTIAL_50_PCT"
    message: str


def check_exit_conditions(
    signal: Signal,
    current_spot: float,
    current_tech: ConfluenceResult,
    entry_spot: float,
    entry_time: datetime,
    atr: float,
    partial_booked: bool = False,
    peak_spot: Optional[float] = None,
) -> ExitDecision:
    """
    Evaluate all exit conditions for a live trade.
    Returns ExitDecision with exit recommendation.
    """
    now = datetime.now()
    minutes_in_trade = (now - entry_time).seconds // 60
    direction = signal.direction

    # ---------- Time-based exit ----------
    _time_exit_min = cfg("layer7", "thresholds", "time_exit_minutes",     default=45)
    _momentum_pct  = cfg("layer7", "thresholds", "momentum_threshold_pct", default=0.15)
    _mom_factor    = 1.0 + _momentum_pct / 100.0
    if minutes_in_trade >= _time_exit_min:
        if direction == Direction.CALL:
            no_momentum = current_spot <= entry_spot * _mom_factor
        else:
            no_momentum = current_spot >= entry_spot / _mom_factor
        if no_momentum:
            return ExitDecision(
                should_exit=True, reason=ExitReason.TIME_EXIT,
                exit_type="FULL",
                message=f"{_time_exit_min} min elapsed with no momentum — time-based exit",
            )

    # ---------- VWAP loss ----------
    if direction == Direction.CALL and current_spot < current_tech.vwap:
        return ExitDecision(
            should_exit=True, reason=ExitReason.VWAP_LOSS,
            exit_type="FULL",
            message=f"Spot {current_spot:.2f} below VWAP {current_tech.vwap:.2f}",
        )
    if direction == Direction.PUT and current_spot > current_tech.vwap:
        return ExitDecision(
            should_exit=True, reason=ExitReason.VWAP_LOSS,
            exit_type="FULL",
            message=f"Spot {current_spot:.2f} reclaimed VWAP {current_tech.vwap:.2f}",
        )

    # ---------- RSI divergence ----------
    if direction == Direction.CALL:
        if peak_spot and current_spot > peak_spot and current_tech.rsi < 60:
            return ExitDecision(
                should_exit=True, reason=ExitReason.RSI_DIVERGENCE,
                exit_type="FULL",
                message=f"Bearish RSI divergence: spot higher but RSI={current_tech.rsi:.1f} fading",
            )
    else:
        if peak_spot and current_spot < peak_spot and current_tech.rsi > 40:
            return ExitDecision(
                should_exit=True, reason=ExitReason.RSI_DIVERGENCE,
                exit_type="FULL",
                message=f"Bullish RSI divergence: spot lower but RSI={current_tech.rsi:.1f} recovering",
            )

    # ---------- Volume exhaustion ----------
    vol_ratio = current_tech.breakdown.get("vol_ratio", 1.0)
    if isinstance(vol_ratio, (int, float)) and vol_ratio < 0.5 and minutes_in_trade > 30:
        return ExitDecision(
            should_exit=True, reason=ExitReason.VOLUME_EXHAUSTION,
            exit_type="FULL",
            message=f"Volume collapsed to {vol_ratio:.2f}x average after 30+ min",
        )

    # ---------- Partial booking at T1 (1:1 RR) ----------
    if not partial_booked and atr > 0:
        if direction == Direction.CALL and current_spot >= entry_spot + atr:
            return ExitDecision(
                should_exit=True, reason=ExitReason.TARGET_1_HIT,
                exit_type="PARTIAL_50_PCT",
                message=f"T1 hit at {current_spot:.2f}. Book 50%. Trail SL to entry {entry_spot:.2f}",
            )
        if direction == Direction.PUT and current_spot <= entry_spot - atr:
            return ExitDecision(
                should_exit=True, reason=ExitReason.TARGET_1_HIT,
                exit_type="PARTIAL_50_PCT",
                message=f"T1 hit at {current_spot:.2f}. Book 50%. Trail SL to entry {entry_spot:.2f}",
            )

    # ---------- Full target at T2 (1:2 RR) ----------
    if atr > 0:
        if direction == Direction.CALL and current_spot >= entry_spot + 2 * atr:
            return ExitDecision(
                should_exit=True, reason=ExitReason.TARGET_2_HIT,
                exit_type="FULL",
                message=f"T2 hit at {current_spot:.2f}. Exit full position.",
            )
        if direction == Direction.PUT and current_spot <= entry_spot - 2 * atr:
            return ExitDecision(
                should_exit=True, reason=ExitReason.TARGET_2_HIT,
                exit_type="FULL",
                message=f"T2 hit at {current_spot:.2f}. Exit full position.",
            )

    return ExitDecision(
        should_exit=False, reason=None,
        exit_type="HOLD",
        message="No exit trigger. Hold with defined SL.",
    )
