from pathlib import Path

_HERE = Path(__file__).parent
BASE_DIR = _HERE.parent

# ── Paths ────────────────────────────────────────────────────────────────────
# DB and log live in ~/nse-fno-screener-data/ — outside iCloud Drive (Desktop/
# Documents are synced and cause sqlite3 "disk I/O error" on every commit).
_DATA_DIR = Path.home() / "nse-fno-screener-data"
_DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH  = _DATA_DIR / "screener_trades.db"
LOG_PATH = _DATA_DIR / "paper_trader.log"

# One-time migration: copy old DB from iCloud-synced project root if present
_OLD_DB = BASE_DIR / "screener_trades.db"
if _OLD_DB.exists() and not DB_PATH.exists():
    import shutil as _shutil
    try:
        _shutil.copy2(str(_OLD_DB), str(DB_PATH))
    except OSError:
        pass

# Instruments cache lives in /tmp to avoid iCloud Drive I/O blocking.
_CACHE_DIR = Path("/tmp/nse-fno-pycache")
_CACHE_DIR.mkdir(parents=True, exist_ok=True)
INSTRUMENTS_CACHE_PATH   = _CACHE_DIR / "instruments_cache.csv"

# ── Strike selection ─────────────────────────────────────────────────────────
ITM_STEPS = 1                       # 1 = first ITM strike

# ── Time rules ───────────────────────────────────────────────────────────────
MARKET_OPEN          = "09:15"
EXIT_BEFORE_CLOSE    = "15:25"      # force-exit all active trades at this time
MARKET_CLOSE_HARD    = "15:30"      # hard market close — no new entries after this

# ── Instruments cache ────────────────────────────────────────────────────────
INSTRUMENTS_REFRESH_HOURS = 24      # re-download instruments CSV after N hours

# ── Retry ────────────────────────────────────────────────────────────────────
RETRY_ATTEMPTS = 3
RETRY_DELAY_S  = 2

# ── Index LTP keys (Kite quote format) ───────────────────────────────────────
INDEX_LTP_KEYS: dict[str, str] = {
    "NIFTY":      "NSE:NIFTY 50",
    "BANKNIFTY":  "NSE:NIFTY BANK",
    "FINNIFTY":   "NSE:NIFTY FIN SERVICE",
    "MIDCPNIFTY": "NSE:NIFTY MIDCAP SELECT",
}

# ── Index spot tokens (hardcoded fallback; instruments.py resolves accurate ones)
INDEX_SPOT_TOKENS: dict[str, int] = {
    "NIFTY":      256265,
    "BANKNIFTY":  260105,
    "FINNIFTY":   257801,
    "MIDCPNIFTY": 288009,
}
