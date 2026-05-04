"""Main screener orchestrator.

Calls all 8 engine layers in sequence and returns a list of Signals.
Each run is independent and stateless; caching is handled per-layer.
"""
import asyncio
import logging
from datetime import datetime
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

            # Skip if regime doesn't support this direction
            if direction == Direction.CALL and not regime.call_buying_environment:
                continue
            if direction == Direction.PUT and not regime.put_buying_environment:
                continue

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
            if tech.score < 50:
                logger.debug(
                    f"[Layer 3] {symbol} score={tech.score:.0f} "
                    f"div={tech.rsi_divergence} htf={tech.htf_trend} — skip"
                )
                continue

            # --- Layer 4: Derivatives intelligence ---
            ltp_map = kite_client.get_ltp([symbol])
            spot = ltp_map.get(symbol, 0.0)
            if spot <= 0:
                continue

            deriv = analyze_derivatives(symbol, spot)
            if direction == Direction.CALL and not deriv.supports_call_buy:
                logger.debug(f"[Layer 4] {symbol} derivatives don't support CALL")
                continue
            if direction == Direction.PUT and not deriv.supports_put_buy:
                logger.debug(f"[Layer 4] {symbol} derivatives don't support PUT")
                continue

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
            )

            if signal:
                logger.info(
                    f"[Layer 6] SIGNAL: {symbol} {direction.value} | "
                    f"Confidence={signal.confidence.value} | Score={signal.gate_score}"
                )
                signals.append(signal)

        except Exception as e:
            logger.error(f"Error processing {candidate.symbol}: {e}", exc_info=True)

    # Sort: A+ > A- > B
    _order = {"A+": 0, "A-": 1, "B": 2}
    signals.sort(key=lambda s: (_order.get(s.confidence.value, 3), -s.gate_score))
    return signals
