from fastapi import APIRouter, Query
from typing import Optional
from app.screener import run_scan
from app.engines.entry_trigger import TradeType

router = APIRouter(prefix="/signals", tags=["signals"])

# In-memory store for the last scan's signals (replaced on each scan)
_last_signals: list = []


def update_signals(signals: list):
    global _last_signals
    _last_signals = signals


@router.get("")
async def get_signals(
    confidence: Optional[str] = Query(default=None, description="Filter by A+, A-, or B"),
    direction: Optional[str] = Query(default=None, enum=["CALL", "PUT"]),
    symbol: Optional[str] = Query(default=None),
):
    results = list(_last_signals)
    if confidence:
        results = [s for s in results if s.confidence.value == confidence]
    if direction:
        results = [s for s in results if s.direction.value == direction]
    if symbol:
        results = [s for s in results if s.symbol.upper() == symbol.upper()]

    return {
        "count": len(results),
        "signals": [
            {
                "id": s.id,
                "timestamp": s.timestamp.isoformat(),
                "symbol": s.symbol,
                "direction": s.direction.value,
                "confidence": s.confidence.value,
                "gate_score": s.gate_score,
                "alert_text": s.to_alert_text(),
            }
            for s in results
        ],
    }


@router.get("/{signal_id}")
async def get_signal(signal_id: str):
    for s in _last_signals:
        if s.id == signal_id:
            return {
                "id": s.id,
                "timestamp": s.timestamp.isoformat(),
                "symbol": s.symbol,
                "direction": s.direction.value,
                "trade_type": s.trade_type.value,
                "confidence": s.confidence.value,
                "gate_score": s.gate_score,
                "gates_passed": s.gates_passed,
                "entry_zone": s.entry_zone,
                "stop_loss": s.stop_loss,
                "target_1": s.target_1,
                "target_2": s.target_2,
                "reasons": s.reasons,
                "option": {
                    "strike": s.option.strike,
                    "expiry": s.option.expiry.isoformat(),
                    "type": s.option.option_type.value,
                    "premium": s.option.current_premium,
                    "iv": s.option.iv,
                    "dte": s.option.days_to_expiry,
                } if s.option else None,
                "alert_text": s.to_alert_text(),
            }
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail="Signal not found")
