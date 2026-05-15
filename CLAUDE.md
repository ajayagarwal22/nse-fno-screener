# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

NSE F&O Options Screener — a FastAPI backend that scans the NSE F&O universe every 5 minutes during market hours, generating high-conviction option-buy signals through an 8-layer pipeline. Only signals passing all 8 layers are emitted (rare by design). Includes a paper trader, Electron desktop monitor, and live web dashboard.

## Commands

**Install:**
```bash
pip install -r requirements.txt
# TA-Lib requires system install first: brew install ta-lib
```

**Run:**
```bash
./start.sh                  # Starts uvicorn at http://localhost:9000
# Or double-click: Open Screener.command (macOS, handles Zerodha OAuth + browser)
```

**Test:**
```bash
pytest                                          # All tests
pytest tests/test_technical.py -v              # Single file
pytest -k "test_bollish_confluence" -v         # Single test
```

**Kite OAuth (token refresh):**
```bash
python kite_auth.py                            # One-shot OAuth token refresh
```

**Desktop app:**
```bash
cd desktop && npm install && npm start
```

**Database migrations:**
```bash
alembic upgrade head
```

## Architecture

### Signal Pipeline (8 Layers)

`app/screener.py::run_scan()` orchestrates all layers sequentially. A signal is only emitted when **all 8 layers pass**:

| Layer | File | Role |
|-------|------|------|
| 1 | `engines/market_regime.py` | Classify market state; gate downstream if Event Risk or Sideways |
| 2 | `engines/stock_selector.py` | Filter 1200+ F&O stocks → top 40 candidates by relative strength |
| 3 | `engines/technical.py` | VWAP/EMA/RSI/MACD/ATR/SuperTrend confluence score (min 45/100) |
| 4 | `engines/derivatives.py` | PCR, Max Pain, OI buildup type, IV skew, writing bias |
| 5 | `engines/option_selector.py` | Pick ATM/1-ITM strike + nearest weekly expiry (no hardcoded strikes) |
| 6 | `engines/entry_trigger.py` | Aggregate all layers → A+/A-/B confidence grades |
| 7 | `engines/exit_engine.py` | Compute SL from spot structure, T1 (1:1 R:R), T2 (1:2 R:R) |
| 8 | `engines/macro_risk.py` | Suppress signals near high-impact events or during FII selling |

### Data Flow

```
APScheduler (every 5 min, 9:15–15:30 IST)
  └─ run_scan()
       ├─ Layer 1–8 per candidate
       └─ Returns [Signal] ranked A+ > A- > B
            ├─ Telegram alert (MarkdownV2)
            ├─ WebSocket broadcast (/ws/alerts)
            ├─ CSV/JSON export (./exports/)
            └─ paper_trader.on_signal() [non-blocking, queued thread]
```

### Key Entry Points

- `app/main.py` — FastAPI app, lifespan (init scheduler + kite + paper_trader), WebSocket at `/ws/alerts`, dashboard at `/`
- `app/screener.py::run_scan()` — stateless orchestrator, called by scheduler and POST `/scan`
- `app/scheduler.py::init_scheduler()` — 3 jobs: scan every 5 min, daily bias at 9:00, EOD export at 15:35
- `paper_trader/__init__.py::on_signal()` — auto-entry, monitors via KiteConnect WebSocket, exits on SL/T1/T2/TIME

### Data Sources

- **Zerodha KiteConnect** — OHLCV (5m/15m/daily), option chain, LTP, instruments list
- **NSE Public APIs** — India VIX, market breadth (A/D ratio), FII/DII flows (`data/nse_client.py`)
- **Yahoo Finance** — USD/INR rate
- **Manual** — `data/events.json` (economic calendar: RBI, Fed, expiry dates)

### Caching (TTL)

- OHLCV: 300s per (token, interval)
- Regime: 300s
- LTP: 30s
- Instruments list: session-long (LRU)

### Paper Trader (`paper_trader/`)

Standalone module with its own SQLite DB (`screener_trades.db`, WAL mode). Uses a dedicated thread + write queue to avoid blocking the async event loop. Strike selection: 1-ITM, nearest weekly expiry. Exits: SL hit, T1, T2, TIME (3:15 PM), MARKET_CLOSE.

### Database (Mostly Unused)

SQLAlchemy models and Alembic migrations exist in `app/models/` and `alembic/`, but **no router currently writes signals to the database** — signals are in-memory only and lost on restart. The paper trades use SQLite separately.

## Environment Variables (`.env`)

Key variables (see `.env.example` for full list):
```
KITE_API_KEY, KITE_API_SECRET, KITE_ACCESS_TOKEN
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
DATABASE_URL=postgresql+asyncpg://...
SCAN_INTERVAL_MINUTES=5
MIN_CONFIDENCE_TO_ALERT=A-
EXPORTS_DIR=./exports
```

## Tests

`tests/conftest.py` provides mock `kite_client` and synthetic OHLCV fixtures. Tests cover Layers 1, 3, 4, and 6. `pytest.ini` sets `asyncio_mode=auto`.

## Known Gaps

- FinNifty / Midcap indices not implemented (only Nifty50 + BankNifty)
- Bollinger Bands mentioned in ARCHITECTURE.md but not in code
- Signal history not persisted to DB (in-memory only — lost on server restart)
