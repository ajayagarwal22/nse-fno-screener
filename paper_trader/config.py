from pathlib import Path

_HERE = Path(__file__).parent
BASE_DIR = _HERE.parent

# ── Paths ────────────────────────────────────────────────────────────────────
DB_PATH                  = BASE_DIR / "screener_trades.db"
LOG_PATH                 = _HERE / "paper_trader.log"

# Instruments cache lives in /tmp to avoid iCloud Drive I/O blocking.
# Falls back to project dir if /tmp is unavailable.
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
