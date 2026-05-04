import asyncio
from fastapi import APIRouter, Query
from app.screener import run_scan, get_candidates
from app.engines.entry_trigger import TradeType
from app.routers.signals import _serialize, update_signals
from app.engines.stock_selector import Candidacy

router = APIRouter(prefix="/scan", tags=["scan"])


@router.post("")
async def trigger_scan(
    trade_type: str = Query(default="INTRADAY", enum=["INTRADAY", "SWING"]),
):
    tt = TradeType.INTRADAY if trade_type == "INTRADAY" else TradeType.SWING
    signals = await run_scan(trade_type=tt)
    update_signals(signals)

    if signals:
        from app.alerts.telegram_bot import send_signals
        asyncio.create_task(send_signals(signals))

    candidates = get_candidates()
    return {
        "count": len(signals),
        "signals": [_serialize(s) for s in signals],
        "candidates": [
            {
                "symbol": c.symbol,
                "direction": "CALL" if c.candidacy == Candidacy.BULLISH else "PUT",
                "rs_score": c.rs_score,
                "volume_ratio": c.volume_ratio,
                "reason": c.reason,
            }
            for c in candidates
        ],
    }
