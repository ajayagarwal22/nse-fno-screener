"""
Suppression filters applied after Layer 6 signal generation.
All suppressions are logged to ~/nse-fno-screener-data/suppression_log.csv

These checks gate signals BEFORE they are appended to the result list or
forwarded to the paper trader.  They are deliberately separate from the
8-layer pipeline so that the pipeline's gate scores and weights remain
untouched.
"""
import csv
import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────

_DATA_DIR = Path.home() / "nse-fno-screener-data"
_DB_PATH = _DATA_DIR / "screener_trades.db"
_LOG_PATH = _DATA_DIR / "suppression_log.csv"
_ADMIN_CONFIG = Path(__file__).parent.parent.parent / "admin" / "admin_config.json"

# ── Module-level cache for daily trend veto ───────────────────────────────────
# { token: (ema_value, last_close, cache_ts) }
_trend_cache: dict = {}
_TREND_CACHE_TTL = 300  # seconds

# ── Default layer8_filters config ─────────────────────────────────────────────

_DEFAULT_LAYER8_FILTERS = {
    "time_of_day_filter": {
        "enabled": True,
        "block_windows": [
            {"start": "11:50", "end": "12:45", "reason": "lunch_chop"}
        ]
    },
    "symbol_cooldown_min": 90,
    "max_simultaneous_per_sector": 2,
    "max_trades_per_day": 10,
    "daily_trend_call_veto": True,
    "daily_trend_index": "NIFTY",
    "daily_trend_ema_period": 20,
}

# ── Sector map for F&O universe ───────────────────────────────────────────────
# Maps NSE symbol → sector bucket.  Only used by check_sector_cap.

_SECTOR_MAP: dict[str, str] = {
    # Banks
    "HDFCBANK": "Banks", "ICICIBANK": "Banks", "SBIN": "Banks",
    "AXISBANK": "Banks", "KOTAKBANK": "Banks", "BANKBARODA": "Banks",
    "BANDHANBNK": "Banks", "FEDERALBNK": "Banks", "IDFCFIRSTB": "Banks",
    "INDUSINDBK": "Banks", "PNB": "Banks", "CANBK": "Banks",
    "UNIONBANK": "Banks", "RBLBANK": "Banks", "AUBANK": "Banks",
    "KARURVYSYA": "Banks", "DCBBANK": "Banks", "INDIANB": "Banks",
    # IT
    "TCS": "IT", "INFY": "IT", "WIPRO": "IT", "HCLTECH": "IT",
    "TECHM": "IT", "LTIM": "IT", "MPHASIS": "IT", "PERSISTENT": "IT",
    "COFORGE": "IT", "LTTS": "IT",
    # Pharma
    "SUNPHARMA": "Pharma", "DRREDDY": "Pharma", "CIPLA": "Pharma",
    "DIVISLAB": "Pharma", "AUROPHARMA": "Pharma", "BIOCON": "Pharma",
    "LUPIN": "Pharma", "TORNTPHARM": "Pharma", "ALKEM": "Pharma",
    "GLENMARK": "Pharma", "IPCALAB": "Pharma",
    # Auto
    "MARUTI": "Auto", "TATAMOTORS": "Auto", "M&M": "Auto",
    "BAJAJ-AUTO": "Auto", "HEROMOTOCO": "Auto", "EICHERMOT": "Auto",
    "ASHOKLEY": "Auto", "TVSMOTOR": "Auto", "BALKRISIND": "Auto",
    "MOTHERSON": "Auto", "BOSCHLTD": "Auto", "EXIDEIND": "Auto",
    # Metals
    "TATASTEEL": "Metals", "JSWSTEEL": "Metals", "HINDALCO": "Metals",
    "VEDL": "Metals", "COALINDIA": "Metals", "NMDC": "Metals",
    "NATIONALUM": "Metals", "HINDCOPPER": "Metals", "SAIL": "Metals",
    # FMCG
    "HINDUNILVR": "FMCG", "ITC": "FMCG", "NESTLEIND": "FMCG",
    "BRITANNIA": "FMCG", "DABUR": "FMCG", "MARICO": "FMCG",
    "GODREJCP": "FMCG", "COLPAL": "FMCG", "EMAMILTD": "FMCG",
    # Energy
    "RELIANCE": "Energy", "ONGC": "Energy", "IOC": "Energy",
    "BPCL": "Energy", "HPCL": "Energy", "GAIL": "Energy",
    "PETRONET": "Energy", "POWERGRID": "Energy", "NTPC": "Energy",
    "TATAPOWER": "Energy", "ADANIPORTS": "Energy", "ADANITRANS": "Energy",
    # PSU
    "LT": "Capital_Goods", "BEL": "PSU", "HAL": "PSU",
    "IRFC": "PSU", "RVNL": "PSU", "RECLTD": "PSU",
    "PFC": "PSU", "BHEL": "PSU", "CONCOR": "PSU",
    # Realty
    "DLF": "Realty", "GODREJPROP": "Realty", "OBEROIRLTY": "Realty",
    "PRESTIGE": "Realty", "BRIGADE": "Realty", "SOBHA": "Realty",
    # Telecom
    "BHARTIARTL": "Telecom", "IDEA": "Telecom",
    # Cement
    "ULTRACEMCO": "Cement", "SHREECEM": "Cement", "ACC": "Cement",
    "AMBUJACEM": "Cement", "RAMCOCEM": "Cement", "JKCEMENT": "Cement",
    # Capital Goods
    "ABB": "Capital_Goods", "SIEMENS": "Capital_Goods",
    "CUMMINSIND": "Capital_Goods", "THERMAX": "Capital_Goods",
    "AIAENG": "Capital_Goods",
    # Consumer Durables
    "VOLTAS": "Consumer_Durables", "WHIRLPOOL": "Consumer_Durables",
    "HAVELLS": "Consumer_Durables", "BLUESTAR": "Consumer_Durables",
    "CROMPTON": "Consumer_Durables", "RAJESHEXPO": "Consumer_Durables",
    # Chemicals
    "PIDILITIND": "Chemicals", "SRF": "Chemicals", "ATUL": "Chemicals",
    "DEEPAKNTR": "Chemicals", "NAVINFLUOR": "Chemicals",
    "VINATIORGA": "Chemicals", "CLEAN": "Chemicals",
    # Indices — own bucket (never capped)
    "NIFTY": "Index", "BANKNIFTY": "Index", "FINNIFTY": "Index",
    "MIDCPNIFTY": "Index", "SENSEX": "Index",
}

_DEFAULT_SECTOR = "Misc"


# ── Config loader ─────────────────────────────────────────────────────────────

def load_suppression_config() -> dict:
    """
    Read admin/admin_config.json and return the layer8_filters subtree.
    Falls back to hardcoded defaults if the file is missing or unreadable.
    """
    try:
        if _ADMIN_CONFIG.exists():
            with open(_ADMIN_CONFIG) as f:
                data = json.load(f)
            cfg = data.get("layer8_filters")
            if isinstance(cfg, dict):
                return cfg
    except Exception as exc:
        logger.warning(f"[Suppression] Could not load admin_config.json: {exc}")
    return dict(_DEFAULT_LAYER8_FILTERS)


# ── Suppression log ───────────────────────────────────────────────────────────

def log_suppression(
    symbol: str,
    direction: str,
    grade: str,
    gate_score: float,
    reason: str,
) -> None:
    """Append one row to ~/nse-fno-screener-data/suppression_log.csv."""
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        write_header = not _LOG_PATH.exists()
        with open(_LOG_PATH, "a", newline="") as fh:
            writer = csv.writer(fh)
            if write_header:
                writer.writerow(
                    ["timestamp", "symbol", "direction", "grade", "gate_score", "suppression_reason"]
                )
            writer.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                symbol,
                direction,
                grade,
                gate_score,
                reason,
            ])
    except Exception as exc:
        logger.warning(f"[Suppression] Could not write suppression log: {exc}")


# ── DB helper ─────────────────────────────────────────────────────────────────

def _db_connect() -> Optional[sqlite3.Connection]:
    """Open a read-only connection to screener_trades.db. Returns None if missing."""
    if not _DB_PATH.exists():
        return None
    conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


# ── Check 1: Time-of-day block ────────────────────────────────────────────────

def check_time_of_day(config: dict) -> Optional[str]:
    """
    Returns a suppression reason string if the current IST time falls inside a
    configured block window, else None.
    """
    tod_cfg = config.get("time_of_day_filter", {})
    if not tod_cfg.get("enabled", True):
        return None

    now_str = datetime.now().strftime("%H:%M")  # local time (IST on server)
    for window in tod_cfg.get("block_windows", []):
        try:
            start = window.get("start", "")
            end = window.get("end", "")
            reason = window.get("reason", "time_block")
            if start and end and start <= now_str < end:
                return f"time_of_day_block:{reason}"
        except Exception:
            continue
    return None


# ── Check 2: Symbol/direction cooldown ────────────────────────────────────────

def check_symbol_cooldown(symbol: str, direction: str, config: dict) -> Optional[str]:
    """
    Returns "cooldown_active" if a signal for this symbol+direction was emitted
    within `symbol_cooldown_min` minutes, else None.
    Queries the signals table in screener_trades.db.
    """
    cooldown_min = config.get("symbol_cooldown_min", 90)
    if cooldown_min <= 0:
        return None

    conn = _db_connect()
    if conn is None:
        return None

    try:
        cur = conn.execute(
            "SELECT MAX(timestamp) FROM signals WHERE symbol = ? AND direction = ?",
            (symbol, direction),
        )
        row = cur.fetchone()
        if row and row[0]:
            last_ts_str = row[0]
            # Timestamps stored as ISO-8601 strings in IST
            try:
                last_ts = datetime.fromisoformat(last_ts_str)
            except ValueError:
                last_ts = datetime.strptime(last_ts_str, "%Y-%m-%d %H:%M:%S")
            elapsed_min = (datetime.now() - last_ts).total_seconds() / 60.0
            if elapsed_min < cooldown_min:
                return "cooldown_active"
    except Exception as exc:
        logger.warning(f"[Suppression] check_symbol_cooldown error: {exc}")
    finally:
        conn.close()

    return None


# ── Check 3: Sector cap ───────────────────────────────────────────────────────

def check_sector_cap(symbol: str, config: dict) -> Optional[str]:
    """
    Returns "sector_cap_hit:<sector>" if there are already
    `max_simultaneous_per_sector` or more ACTIVE/WATCHING trades in the same
    sector as `symbol`, else None.
    Indices are never capped (sector == "Index").
    """
    sector = _SECTOR_MAP.get(symbol, _DEFAULT_SECTOR)
    if sector == "Index":
        return None

    cap = config.get("max_simultaneous_per_sector", 2)
    if cap <= 0:
        return None

    conn = _db_connect()
    if conn is None:
        return None

    try:
        # Fetch all WATCHING/ACTIVE symbols to count by sector in Python
        # (avoids encoding the whole sector map in SQL)
        cur = conn.execute(
            "SELECT symbol FROM trades WHERE status IN ('WATCHING', 'ACTIVE')"
        )
        rows = cur.fetchall()
        sector_count = sum(
            1 for r in rows if _SECTOR_MAP.get(r[0], _DEFAULT_SECTOR) == sector
        )
        if sector_count >= cap:
            return f"sector_cap_hit:{sector}"
    except Exception as exc:
        logger.warning(f"[Suppression] check_sector_cap error: {exc}")
    finally:
        conn.close()

    return None


# ── Check 4: Daily trade cap ──────────────────────────────────────────────────

def check_daily_cap(config: dict) -> Optional[str]:
    """
    Returns "daily_cap_hit" if today's non-SKIPPED trade count has reached
    `max_trades_per_day`, else None.
    """
    max_trades = config.get("max_trades_per_day", 10)
    if max_trades <= 0:
        return None

    conn = _db_connect()
    if conn is None:
        return None

    try:
        cur = conn.execute(
            """
            SELECT COUNT(*) FROM trades
            WHERE date(created_at) = date('now', 'localtime')
              AND status != 'SKIPPED'
            """
        )
        row = cur.fetchone()
        count = row[0] if row else 0
        if count >= max_trades:
            return "daily_cap_hit"
    except Exception as exc:
        logger.warning(f"[Suppression] check_daily_cap error: {exc}")
    finally:
        conn.close()

    return None


# ── Check 5: Daily trend CALL veto ────────────────────────────────────────────

def check_daily_trend_veto(
    symbol: str,
    direction: str,
    config: dict,
    kite_client,
) -> Optional[str]:
    """
    Only applies to CALL signals.
    Fetches daily OHLCV for the configured index token, computes EMA of
    `daily_trend_ema_period` on daily closes (pandas ewm), and vetoes the
    signal if the last close < EMA.
    Result is cached for 300 seconds.
    """
    if direction != "CALL":
        return None

    if not config.get("daily_trend_call_veto", True):
        return None

    ema_period = config.get("daily_trend_ema_period", 20)
    # NIFTY token is always 256265; extend mapping if needed
    _index_tokens = {"NIFTY": 256265, "BANKNIFTY": 260105}
    index_name = config.get("daily_trend_index", "NIFTY")
    token = _index_tokens.get(index_name, 256265)

    # Check cache
    cached = _trend_cache.get(token)
    if cached:
        ema_val, last_close, cache_ts = cached
        if time.time() - cache_ts < _TREND_CACHE_TTL:
            if last_close < ema_val:
                return "daily_trend_veto"
            return None

    # Fetch fresh data — import inside function to avoid circular imports
    try:
        import pandas as pd

        df = kite_client.get_ohlcv(token, interval="day")
        if df is None or df.empty or len(df) < ema_period:
            return None  # insufficient data — do not veto

        closes = df["close"].astype(float)
        ema_series = closes.ewm(span=ema_period, adjust=False).mean()
        last_close = float(closes.iloc[-1])
        ema_val = float(ema_series.iloc[-1])

        _trend_cache[token] = (ema_val, last_close, time.time())
        logger.debug(
            f"[Suppression] DailyTrend {index_name}: close={last_close:.2f} "
            f"EMA{ema_period}={ema_val:.2f}"
        )

        if last_close < ema_val:
            return "daily_trend_veto"
    except Exception as exc:
        logger.warning(f"[Suppression] check_daily_trend_veto error: {exc}")

    return None
