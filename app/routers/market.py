from fastapi import APIRouter
from app.engines.market_regime import analyze_market_regime
from app.engines.macro_risk import assess_macro_risk
from app.data import cache

router = APIRouter(prefix="/market", tags=["market"])


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
