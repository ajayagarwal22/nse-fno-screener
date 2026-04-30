from fastapi import APIRouter, Query
from app.screener import run_scan
from app.engines.entry_trigger import TradeType

router = APIRouter(prefix="/scan", tags=["scan"])


@router.post("")
async def trigger_scan(
    trade_type: str = Query(default="INTRADAY", enum=["INTRADAY", "SWING"]),
):
    tt = TradeType.INTRADAY if trade_type == "INTRADAY" else TradeType.SWING
    signals = await run_scan(trade_type=tt)
    return {
        "count": len(signals),
        "signals": [
            {
                "id": s.id,
                "symbol": s.symbol,
                "direction": s.direction.value,
                "trade_type": s.trade_type.value,
                "confidence": s.confidence.value,
                "gate_score": s.gate_score,
                "strike": s.option.strike if s.option else None,
                "expiry": s.option.expiry.isoformat() if s.option else None,
                "option_type": s.option.option_type.value if s.option else None,
                "premium": s.option.current_premium if s.option else None,
                "entry_zone": s.entry_zone,
                "stop_loss": s.stop_loss,
                "target_1": s.target_1,
                "target_2": s.target_2,
                "rr_ratio": s.rr_ratio,
                "position_sizing": s.position_sizing,
                "reasons": s.reasons,
                "regime_type": s.regime_type,
                "vix_level": s.vix_level,
                "rsi_value": s.rsi_value,
                "pcr_value": s.pcr_value,
                "alert_text": s.to_alert_text(),
            }
            for s in signals
        ],
    }
