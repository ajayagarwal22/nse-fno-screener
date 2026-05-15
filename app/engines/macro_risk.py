"""Layer 8 — Macro Risk Engine.

Maintains an economic calendar and monitors macro factors that suppress
signal generation. Returns True when trading risk is elevated.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import json
from typing import Optional

import httpx

from app.config import settings

# ---------------------------------------------------------------------------
# Economic calendar — hardcoded high-impact events (extend via events.json)
# ---------------------------------------------------------------------------

_BUILTIN_EVENTS: list[dict] = [
    # Format: {"date": "YYYY-MM-DD", "name": "...", "impact": "HIGH"}
]

_CALENDAR_FILE = Path(__file__).parent.parent.parent / "data" / "events.json"


@dataclass
class MacroRiskAssessment:
    is_high_risk: bool
    reasons: list[str]
    upcoming_events: list[dict]
    fii_net_flow: Optional[float]    # crores, positive = buying
    usdinr: Optional[float]
    vix_us: Optional[float]


def _load_calendar() -> list[dict]:
    events = list(_BUILTIN_EVENTS)
    if _CALENDAR_FILE.exists():
        try:
            with open(_CALENDAR_FILE) as f:
                events.extend(json.load(f))
        except Exception:
            pass
    return events


def _events_in_window(events: list[dict], window_hours: int) -> list[dict]:
    now = datetime.now()
    upcoming = []
    for ev in events:
        try:
            ev_date = datetime.strptime(ev["date"], "%Y-%m-%d")
            delta = abs((ev_date - now).total_seconds() / 3600)
            if delta <= window_hours:
                upcoming.append(ev)
        except Exception:
            continue
    return upcoming


def _fetch_fii_flow() -> Optional[float]:
    """Fetch FII net flow from NSE. Returns crores (positive = net buy)."""
    url = "https://www.nseindia.com/api/fiidiiTradeReact"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.nseindia.com/",
    }
    try:
        with httpx.Client(headers=headers, timeout=8, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
            for row in data:
                if row.get("category") == "FII/FPI":
                    return float(row.get("netVal", 0))
    except Exception:
        pass
    return None


def _fetch_usdinr() -> Optional[float]:
    """Fetch USD/INR via Yahoo Finance (free endpoint). Tries query1 then query2."""
    _headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://finance.yahoo.com/",
    }
    for host in ("query1", "query2"):
        url = f"https://{host}.finance.yahoo.com/v8/finance/chart/USDINR=X?interval=1d&range=1d"
        try:
            with httpx.Client(headers=_headers, timeout=8, follow_redirects=True) as client:
                resp = client.get(url)
                if resp.status_code == 429:
                    continue
                resp.raise_for_status()
                result = resp.json()
                price = result["chart"]["result"][0]["meta"]["regularMarketPrice"]
                return float(price)
        except Exception:
            continue
    return None


def assess_macro_risk() -> MacroRiskAssessment:
    """Main entry for Layer 8. Suppress signals if this returns is_high_risk=True."""
    events = _load_calendar()
    upcoming = _events_in_window(events, settings.high_risk_window_hours)

    reasons: list[str] = []

    if upcoming:
        for ev in upcoming:
            reasons.append(f"High-impact event within {settings.high_risk_window_hours}h: {ev['name']}")

    fii_flow = _fetch_fii_flow()
    usdinr = _fetch_usdinr()

    # Macro stress signals
    if usdinr and usdinr > 85.0:
        reasons.append(f"USD/INR elevated at {usdinr:.2f} — currency stress")

    if fii_flow is not None and fii_flow < -3000:
        reasons.append(f"FII heavy selling: ₹{fii_flow:,.0f} cr net outflow")

    is_high_risk = len(upcoming) > 0

    return MacroRiskAssessment(
        is_high_risk=is_high_risk,
        reasons=reasons,
        upcoming_events=upcoming,
        fii_net_flow=fii_flow,
        usdinr=usdinr,
        vix_us=None,
    )
