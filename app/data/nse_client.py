"""NSE public data: India VIX, market breadth, advance/decline."""
import httpx
from dataclasses import dataclass


NSE_VIX_URL = "https://www.nseindia.com/api/allIndices"
NSE_ADVANCE_DECLINE_URL = "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%20500"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "Accept": "application/json",
    "Referer": "https://www.nseindia.com/",
}


@dataclass
class VIXData:
    value: float
    change: float
    change_pct: float
    signal: str  # LOW_VIX | NORMAL | HIGH_VIX | EXTREME_VIX


@dataclass
class MarketBreadth:
    advances: int
    declines: int
    unchanged: int
    advance_decline_ratio: float
    breadth_score: float  # 0–100, >60 bullish, <40 bearish


def _classify_vix(vix: float) -> str:
    if vix < 13:
        return "LOW_VIX"
    elif vix <= 18:
        return "NORMAL"
    elif vix <= 25:
        return "HIGH_VIX"
    return "EXTREME_VIX"


def fetch_vix() -> VIXData:
    """Fetch India VIX from NSE. Falls back to a neutral default on failure."""
    try:
        with httpx.Client(headers=_HEADERS, timeout=10, follow_redirects=True) as client:
            resp = client.get(NSE_VIX_URL)
            resp.raise_for_status()
            data = resp.json()
            indices = data.get("data", [])
            for idx in indices:
                if idx.get("index") == "India VIX":
                    value = float(idx.get("last", 15.0))
                    change = float(idx.get("variation", 0.0))
                    change_pct = float(idx.get("percentChange", 0.0))
                    return VIXData(
                        value=value,
                        change=change,
                        change_pct=change_pct,
                        signal=_classify_vix(value),
                    )
    except Exception:
        pass
    return VIXData(value=15.0, change=0.0, change_pct=0.0, signal="NORMAL")


def fetch_market_breadth() -> MarketBreadth:
    """Fetch Nifty 500 advance/decline from NSE."""
    try:
        with httpx.Client(headers=_HEADERS, timeout=10, follow_redirects=True) as client:
            resp = client.get(NSE_ADVANCE_DECLINE_URL)
            resp.raise_for_status()
            data = resp.json()
            advances = int(data.get("advance", {}).get("advances", 25))
            declines = int(data.get("advance", {}).get("declines", 25))
            unchanged = int(data.get("advance", {}).get("unchanged", 0))
            total = advances + declines + unchanged or 1
            adr = advances / max(declines, 1)
            breadth_score = (advances / total) * 100
            return MarketBreadth(
                advances=advances,
                declines=declines,
                unchanged=unchanged,
                advance_decline_ratio=round(adr, 2),
                breadth_score=round(breadth_score, 1),
            )
    except Exception:
        return MarketBreadth(
            advances=25, declines=25, unchanged=0,
            advance_decline_ratio=1.0, breadth_score=50.0,
        )
