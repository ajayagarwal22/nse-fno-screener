import time
from datetime import date
from fastapi import APIRouter
from app.engines.market_regime import analyze_market_regime
from app.engines.macro_risk import assess_macro_risk
from app.data import cache

router = APIRouter(prefix="/market", tags=["market"])

# Prev-close cache — seeded once via ohlc(), then LTP is used for every tick
_prev_close: dict[str, float] = {}

# Nearest Nifty futures key (e.g. "NFO:NIFTY26MAYFUT") — refreshed hourly
_fut_key: str | None = None
_fut_key_ts: float = 0.0

_KITE_KEYS: dict[str, str] = {
    "NIFTY":     "NSE:NIFTY 50",
    "BANKNIFTY": "NSE:NIFTY BANK",
    "SENSEX":    "BSE:SENSEX",
}


def _nearest_nifty_fut_key() -> str | None:
    """Return the kite.ltp() key for the nearest-expiry Nifty futures. Cached 1 h."""
    global _fut_key, _fut_key_ts
    if _fut_key and time.time() - _fut_key_ts < 3600:
        return _fut_key
    try:
        from app.data.kite_client import kite_client
        df = kite_client.get_fno_instruments()
        fut = df[(df["name"] == "NIFTY") & (df["instrument_type"] == "FUT")]
        fut = fut[fut["expiry"] >= date.today()].sort_values("expiry")
        if not fut.empty:
            _fut_key = f"NFO:{fut.iloc[0]['tradingsymbol']}"
            _fut_key_ts = time.time()
            return _fut_key
    except Exception:
        pass
    return None


@router.get("/regime")
async def get_regime():
    cached = cache.get_regime()
    if cached:
        regime = cached
    else:
        regime = analyze_market_regime()
    return {
        "regime_type": regime.regime_type.value,
        "overall_bias": regime.overall_bias.value,
        "nifty_bias": regime.nifty_bias.value,
        "banknifty_bias": regime.banknifty_bias.value,
        "vix": regime.vix_data.value,
        "vix_signal": regime.vix_data.signal,
        "breadth_score": regime.breadth.breadth_score,
        "advance_decline_ratio": regime.breadth.advance_decline_ratio,
        "call_buying_environment": regime.call_buying_environment,
        "put_buying_environment": regime.put_buying_environment,
        "reason": regime.reason,
    }


@router.get("/breadth")
async def get_breadth():
    from app.data.nse_client import fetch_market_breadth
    breadth = fetch_market_breadth()
    return {
        "advances": breadth.advances,
        "declines": breadth.declines,
        "unchanged": breadth.unchanged,
        "advance_decline_ratio": breadth.advance_decline_ratio,
        "breadth_score": breadth.breadth_score,
    }


@router.get("/indices")
async def get_indices():
    """
    Returns NIFTY FUT (nearest), NIFTY, BANKNIFTY, SENSEX.
    First call uses kite.ohlc() to seed the prev-close cache;
    all subsequent calls use the faster kite.ltp() — a single batch request.
    """
    global _prev_close
    from app.data.kite_client import kite_client

    fut_key = _nearest_nifty_fut_key()

    # Build full key map including futures
    key_map = dict(_KITE_KEYS)
    if fut_key:
        key_map["NIFTY FUT"] = fut_key

    # Seed prev-close once per session via ohlc (includes daily close)
    if not _prev_close:
        try:
            ohlc = kite_client.kite.ohlc(list(key_map.values()))
            for name, key in key_map.items():
                pc = (ohlc.get(key) or {}).get("ohlc", {}).get("close") or 0
                _prev_close[name] = float(pc)
        except Exception:
            pass

    # Single batch LTP call — very fast
    result = []
    try:
        ltp_data = kite_client.kite.ltp(list(key_map.values()))
        ordered = ["NIFTY", "SENSEX", "BANKNIFTY"] + (["NIFTY FUT"] if fut_key else [])
        for name in ordered:
            key = key_map.get(name)
            if not key:
                continue
            ltp = float((ltp_data.get(key) or {}).get("last_price") or 0)
            prev = _prev_close.get(name) or ltp
            change = ltp - prev
            change_pct = (change / prev * 100) if prev else 0.0
            result.append({
                "name": name,
                "ltp": round(ltp, 2),
                "change": round(change, 2),
                "change_pct": round(change_pct, 2),
            })
    except Exception:
        pass

    return {"indices": result}


@router.get("/macro-risk")
async def get_macro_risk():
    assessment = assess_macro_risk()
    return {
        "is_high_risk": assessment.is_high_risk,
        "reasons": assessment.reasons,
        "upcoming_events": assessment.upcoming_events,
        "fii_net_flow_cr": assessment.fii_net_flow,
        "usdinr": assessment.usdinr,
    }
