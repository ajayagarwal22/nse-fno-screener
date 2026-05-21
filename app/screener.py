"""Main screener orchestrator.

Calls all 8 engine layers in sequence and returns a list of Signals.
Each run is independent and stateless; caching is handled per-layer.
"""
import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.data.kite_client import kite_client
from app.engines.derivatives import analyze_derivatives
from app.engines.entry_trigger import Direction, Signal, TradeType, evaluate_signal
from app.engines.exit_engine import ExitDecision
from app.engines.macro_risk import assess_macro_risk
from app.engines.market_regime import MarketRegime, analyze_market_regime
from app.engines.option_selector import OptionType, select_option
from app.engines.stock_selector import Candidacy, select_candidates
from app.engines.technical import score_bearish_confluence, score_bullish_confluence

logger = logging.getLogger(__name__)

_ADMIN_CONFIG_PATH = Path(__file__).parent.parent / "admin" / "admin_config.json"


def _get_layer3_min_score() -> int:
    """
    Read layer3.thresholds.min_score from admin_config.json.
    Returns 60 if the file is missing or unreadable.
    """
    try:
        if _ADMIN_CONFIG_PATH.exists():
            with open(_ADMIN_CONFIG_PATH) as f:
                data = json.load(f)
            val = data.get("layer3", {}).get("thresholds", {}).get("min_score")
            if isinstance(val, (int, float)):
                return int(val)
    except Exception as exc:
        logger.warning(f"[Screener] Could not read layer3 min_score from config: {exc}")
    return 60

# Indices instrument tokens (NSE)
_NIFTY_TOKEN = 256265
_BANKNIFTY_TOKEN = 260105

_last_candidates: list = []


def get_candidates() -> list:
    return _last_candidates


def _fetch_index_ohlcv(token: int):
    try:
        return kite_client.get_ohlcv(token, interval="5minute")
    except Exception as e:
        logger.warning(f"Could not fetch index data for token {token}: {e}")
        return None


async def run_scan(trade_type: TradeType = TradeType.INTRADAY) -> list[Signal]:
    """
    Full 8-layer scan. Returns ranked list of Signals (A+ first).
    """
    signals: list[Signal] = []

    # --- Layer 8 first: macro gate ---
    macro = assess_macro_risk()
    if macro.is_high_risk:
        logger.info(f"[Layer 8] High macro risk — suppressing scan. Reasons: {macro.reasons}")
        return []

    # --- Layer 1: Market Regime ---
    nifty_df = _fetch_index_ohlcv(_NIFTY_TOKEN)
    bnf_df = _fetch_index_ohlcv(_BANKNIFTY_TOKEN)
    regime: MarketRegime = analyze_market_regime(nifty_df=nifty_df, banknifty_df=bnf_df)
    logger.info(f"[Layer 1] Regime: {regime.regime_type.value} | Bias: {regime.overall_bias.value}")

    # Early exit: theta decay or event risk environments
    if regime.regime_type.value in ("THETA_DECAY", "EVENT_RISK"):
        logger.info(f"[Layer 1] Regime={regime.regime_type.value} — no option buying signals")
        return []

    # --- Layer 2: Stock selection ---
    nifty_daily_df = kite_client.get_ohlcv(_NIFTY_TOKEN, interval="day")
    candidates = select_candidates(regime, nifty_daily_df=nifty_daily_df)
    global _last_candidates
    _last_candidates = candidates
    logger.info(f"[Layer 2] {len(candidates)} candidates shortlisted")

    # Process each candidate through Layers 3–6
    for candidate in candidates:
        try:
            symbol = candidate.symbol
            direction = (
                Direction.CALL if candidate.candidacy == Candidacy.BULLISH else Direction.PUT
            )

            # --- Layer 3: Technical confluence (MTF) ---
            token = candidate.instrument_token
            df_5min = kite_client.get_ohlcv(token, interval="5minute")
            if df_5min is None or df_5min.empty:
                continue
            df_15min = kite_client.get_ohlcv(token, interval="15minute")
            df_daily = kite_client.get_ohlcv(token, interval="day")

            tech = (
                score_bullish_confluence(df_5min, df_mtf=df_15min, df_htf=df_daily)
                if direction == Direction.CALL
                else score_bearish_confluence(df_5min, df_mtf=df_15min, df_htf=df_daily)
            )
            # Index candidates use a lower Layer 3 threshold — their regime alignment
            # is already confirmed at candidacy level (nifty_bias / banknifty_bias).
            layer3_min = 30 if candidate.is_index else _get_layer3_min_score()
            if tech.score < layer3_min:
                logger.debug(
                    f"[Layer 3] {symbol} score={tech.score:.0f} "
                    f"div={tech.rsi_divergence} htf={tech.htf_trend} — skip"
                )
                continue

            # Block counter-trend setups without divergence (most common fake signal)
            # Index candidates are exempt — their direction is set by regime bias directly.
            if not candidate.is_index:
                if direction == Direction.CALL and tech.htf_trend == "BEARISH" and not tech.rsi_divergence:
                    logger.debug(f"[Layer 3] {symbol} CALL vs BEARISH daily — no divergence — skip")
                    continue
                if direction == Direction.PUT and tech.htf_trend == "BULLISH" and not tech.rsi_divergence:
                    logger.debug(f"[Layer 3] {symbol} PUT vs BULLISH daily — no divergence — skip")

            # --- Layer 4: Derivatives intelligence ---
            ltp_map = kite_client.get_ltp([symbol])
            spot = ltp_map.get(symbol, 0.0)
            if spot <= 0:
                continue

            deriv = analyze_derivatives(symbol, spot)

            # --- Layer 5: Option selection ---
            opt_type = OptionType.CE if direction == Direction.CALL else OptionType.PE
            option = select_option(symbol, spot, opt_type, trade_type, regime)

            # --- Layer 6: Entry trigger ---
            signal = evaluate_signal(
                symbol=symbol,
                direction=direction,
                trade_type=trade_type,
                spot=spot,
                regime=regime,
                tech=tech,
                deriv=deriv,
                option=option,
                rs_score=candidate.rs_score,
                force_regime_ok=candidate.is_index,
            )

            if signal:
                logger.info(
                    f"[Layer 6] SIGNAL: {symbol} {direction.value} | "
                    f"Confidence={signal.confidence.value} | Score={signal.gate_score}"
                )

                # Post-Layer-6 suppression filters
                from app.engines.suppression_filter import (
                    check_time_of_day, check_symbol_cooldown, check_sector_cap,
                    check_daily_cap, check_daily_trend_veto, log_suppression,
                    load_suppression_config,
                )
                _sup_cfg = load_suppression_config()
                _sup_reason = (
                    check_time_of_day(_sup_cfg) or
                    check_symbol_cooldown(symbol, direction.value, _sup_cfg) or
                    check_sector_cap(symbol, _sup_cfg) or
                    check_daily_cap(_sup_cfg) or
                    check_daily_trend_veto(symbol, direction.value, _sup_cfg, kite_client)
                )
                if _sup_reason:
                    log_suppression(
                        symbol, direction.value,
                        signal.confidence.value, signal.gate_score,
                        _sup_reason,
                    )
                    logger.info(
                        f"[Suppression] {symbol} {direction.value} blocked: {_sup_reason}"
                    )
                    continue

                signals.append(signal)
                import paper_trader; paper_trader.on_signal(signal)

        except Exception as e:
            logger.error(f"Error processing {candidate.symbol}: {e}", exc_info=True)

    # Sort: A+ > A- > B
    _order = {"A+": 0, "A-": 1, "B": 2}
    signals.sort(key=lambda s: (_order.get(s.confidence.value, 3), -s.gate_score))
    return signals
