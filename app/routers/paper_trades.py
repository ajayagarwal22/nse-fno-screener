"""Paper trader REST endpoints.

GET  /paper-trades/status       — is paper_trader initialised, how many active trades
POST /paper-trades/test-signal  — fire a synthetic signal to test end-to-end pipeline
GET  /paper-trades/             — today's trades from DB
GET  /paper-trades/summary      — win rate / P&L aggregates
"""
import sqlite3
from datetime import date
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/paper-trades", tags=["paper-trades"])


# ── Status ────────────────────────────────────────────────────────────────────

@router.get("/status")
def paper_trader_status():
    import paper_trader as pt
    instance = pt._instance
    if instance is None:
        return {"initialised": False}
    monitor = instance._monitor
    with monitor._lock:
        active = len(monitor._trades)
    return {
        "initialised": True,
        "active_trades": active,
        "ticker_connected": instance._ticker is not None,
        "subscribed_tokens": len(instance._subscribed_tokens),
        "queue_size": instance._queue.qsize(),
    }


# ── Test signal ───────────────────────────────────────────────────────────────

class TestSignalRequest(BaseModel):
    symbol: str = "NIFTY"
    direction: str = "CALL"  # "CALL" or "PUT"


@router.post("/test-signal")
def fire_test_signal(req: TestSignalRequest):
    """
    Inject a synthetic signal into paper_trader to verify the full pipeline:
    DB insert → strike pick → LTP fetch → trade entry → monitor subscription.
    """
    import paper_trader as pt
    if pt._instance is None:
        raise HTTPException(503, "paper_trader not initialised — start the server first")

    from app.data.kite_client import kite_client
    try:
        ltp_map = kite_client.get_ltp([req.symbol])
        spot = ltp_map.get(req.symbol, 0.0)
    except Exception:
        spot = 0.0

    if spot <= 0:
        spot = 24000.0 if req.symbol == "NIFTY" else 52000.0

    atr = spot * 0.005
    direction = req.direction.upper()
    entry_spot = spot

    mock_signal = {
        "symbol": req.symbol,
        "direction": direction,
        "confidence": "A-",
        "gate_score": 70,
        "entry_zone": f"Premium breakout above {entry_spot:.2f} zone",
        "stop_loss": f"Spot closes below {entry_spot - atr:.2f}",
        "target_1": f"{entry_spot + atr:.2f} (1:1 RR)",
        "target_2": f"{entry_spot + 2*atr:.2f} (1:2 RR)",
        "rr_ratio": "1:2",
        "vwap_status": f"Above VWAP ({entry_spot - 10:.2f})",
        "macd_status": "Bullish cross (hist=+0.123)",
        "vix_level": 14.5,
        "pcr_value": 0.95,
        "oi_interpretation": "Short covering in progress",
        "htf_trend": "BULLISH",
        "divergence_detected": True,
        "rsi_value": 58.0,
        "gates_passed": {
            "regime_supportive": True,
            "rs_positive": True,
            "rsi_divergence": True,
            "htf_trend_bullish": True,
        },
        "time_sensitivity": "Avoid holding after 2:30 PM if momentum fades.",
        "position_sizing": "Standard position (1.5–2% capital)",
        "timestamp": __import__("datetime").datetime.now(),
    }

    pt.on_signal(mock_signal)
    return {
        "status": "queued",
        "symbol": req.symbol,
        "direction": direction,
        "spot_used": entry_spot,
        "message": "Check /paper-trades/ in ~3 seconds to see the recorded trade",
    }


# ── Today's trades ────────────────────────────────────────────────────────────

@router.get("/")
def list_trades(limit: int = 50):
    from paper_trader.config import DB_PATH
    try:
        con = sqlite3.connect(str(DB_PATH))
        con.row_factory = sqlite3.Row
        today = date.today().isoformat()
        rows = con.execute(
            """
            SELECT t.*, s.grade, s.confidence as gate_score, s.htf_trend, s.divergence
            FROM trades t
            LEFT JOIN signals s ON t.signal_id = s.id
            WHERE date(t.entry_time) = ?
               OR t.status IN ('ACTIVE','WATCHING')
            ORDER BY t.id DESC LIMIT ?
            """,
            (today, limit),
        ).fetchall()
        con.close()
        trades = [dict(r) for r in rows]

        # Merge live prices for WATCHING/ACTIVE trades from in-memory monitor
        import paper_trader as pt
        if pt._instance is not None:
            live = pt._instance._monitor.get_live_prices()
            for t in trades:
                if t["id"] in live:
                    lp = live[t["id"]]
                    t["current_spot"]    = lp.get("current_spot")
                    t["current_premium"] = lp.get("current_premium")
                    t["live_status"]     = lp.get("status")

        return {"trades": trades}
    except Exception as exc:
        raise HTTPException(500, str(exc))


# ── Summary ───────────────────────────────────────────────────────────────────

@router.get("/summary")
def summary():
    from paper_trader.config import DB_PATH
    try:
        con = sqlite3.connect(str(DB_PATH))
        con.row_factory = sqlite3.Row
        overall = con.execute("SELECT * FROM accuracy_overall").fetchone()
        by_grade = con.execute("SELECT * FROM accuracy_by_grade ORDER BY grade").fetchall()
        extras = con.execute("""
            SELECT
                SUM(pnl_points)  AS total_pnl_points,
                SUM(pnl_rupees)  AS total_pnl_rupees,
                COUNT(*) FILTER (WHERE status IN ('ACTIVE','WATCHING')) AS active_count
            FROM trades WHERE status != 'SKIPPED'
        """).fetchone()
        con.close()
        overall_dict = dict(overall) if overall else {}
        if extras:
            overall_dict["total_pnl_points"] = extras["total_pnl_points"]
            overall_dict["total_pnl_rupees"] = extras["total_pnl_rupees"]
        return {
            "overall": overall_dict,
            "by_grade": [dict(r) for r in by_grade],
            "_active_count": extras["active_count"] if extras else 0,
        }
    except Exception as exc:
        raise HTTPException(500, str(exc))
