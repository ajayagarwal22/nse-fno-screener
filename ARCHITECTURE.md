# Architecture & Design Context

This document captures the original design intent for the NSE F&O Screener so the reasoning behind the codebase is not lost.

---

## Origin

The project was built from a detailed prompt written as a "Senior Quantitative Market Strategist with 30+ years of NSE experience." The full specification described a professional-grade 8-layer options screener that answers one question:

> *"Is the probability-adjusted risk-to-reward favorable RIGHT NOW?"*

The core philosophy:

> *"The perfect screener is NOT designed to find more trades. It is designed to filter OUT low-probability trades."*

---

## The 8-Layer Pipeline

Every layer is a filter. A signal is only emitted when all 8 pass.

### Layer 1 — Market Regime Engine (`engines/market_regime.py`)
Classifies the market into one of 7 states using India VIX, Nifty/BankNifty structure, VWAP positioning, and breadth:
- `TRENDING_BULLISH` / `TRENDING_BEARISH`
- `RANGEBOUND`
- `MEAN_REVERTING`
- `HIGH_VOL_EXPANSION`
- `EVENT_RISK`
- `THETA_DECAY`

VIX interpretation:
- `< 13` → premiums cheap → favour option buying
- `13–18` → normal
- `> 18–20` → expensive premiums → avoid late option buying
- `> 25` → extreme → no signal generation

All downstream layers gate on this output.

### Layer 2 — Stock Selection Engine (`engines/stock_selector.py`)
Filters the full NSE F&O universe to ranked long/short candidates:
- Relative strength vs Nifty (20-day cumulative return differential)
- Volume expansion ≥ 1.5× 20-day average
- Option chain liquidity (OI > threshold, bid-ask spread < 0.5%)
- Rejects illiquid options, wide spreads, low OI strikes

Outputs: top 20 bullish candidates + top 20 bearish candidates.

### Layer 3 — Technical Confluence Engine (`engines/technical.py`)
Indicators are **never used in isolation** — only a combined confluence score (0–100) passes.

Indicators: VWAP, EMA 20/50/200, RSI(14), MACD(12,26,9), ATR(14), Supertrend.
Uses TA-Lib if installed; falls back to pure-pandas implementation.

Perfect bullish (score ≥ 80):
- Price above VWAP
- EMA 20 > EMA 50 > EMA 200
- RSI 55–70 rising
- MACD histogram positive and expanding
- Volume > 1.5× average

### Layer 4 — Derivatives Intelligence Engine (`engines/derivatives.py`)
Reads the option chain and interprets institutional positioning:
- **PCR**: `< 0.7` bearish, `0.7–1.3` neutral, `> 1.3` bullish, `> 1.5` overheated
- **Max Pain**: strike minimising total OI writer loss
- **OI Buildup classification**: Long Buildup / Short Buildup / Short Covering / Long Unwinding
- **Writing bias**: call writing (bearish) vs put writing (bullish) near ATM
- **IV skew**: PE IV minus CE IV at ATM

### Layer 5 — Option Selection Engine (`engines/option_selector.py`)
Picks the right contract dynamically (no hardcoded strikes):
- Intraday: ATM or 1-strike ITM, nearest weekly expiry
- Swing: 1-strike ITM, 2–4 week expiry
- Filters: OI > 500, bid-ask spread < threshold, no far OTM lottery options

### Layer 6 — Entry Trigger Engine (`engines/entry_trigger.py`)
Aggregates all 5 layers. Emits a signal only when all gates pass.

Gate weights (call buy, total = 100):
| Gate | Weight |
|---|---|
| Regime supportive | 15 |
| Above VWAP | 15 |
| EMA bullish alignment | 15 |
| RS positive vs Nifty | 10 |
| RSI 55–75 | 10 |
| MACD positive + expanding | 10 |
| Put writing or short covering | 10 |
| PCR supportive | 10 |
| Volume breakout | 5 |

Confidence grading:
- `A+` — all gates pass, score ≥ 95 → full position (2–3% capital)
- `A-` — 8/9 gates, score ≥ 75 → standard position (1.5–2% capital)
- `B` — 6+/9 gates, score ≥ 55 → half position (0.5–1% capital)
- Below B → no signal emitted

### Layer 7 — Exit Engine (`engines/exit_engine.py`)
Stop losses are always computed on **spot price structure, never option premium**.

Exit triggers (checked on every scan):
- Partial booking (50%) when T1 hit (1:1 RR)
- Full exit at T2 (1:2 RR)
- VWAP loss
- RSI divergence
- Volume exhaustion (< 0.5× average after 30 min)
- Time-based exit (no momentum after 60 min, intraday)
- Trailing stop after T1 hit (SL moves to entry)

### Layer 8 — Macro Risk Engine (`engines/macro_risk.py`)
Can veto any signal regardless of technical confluence.

Monitors:
- Economic calendar (RBI, Fed, Budget, elections, earnings) — from `data/events.json`
- FII/DII net flows from NSE
- USD/INR (Yahoo Finance)
- Suppresses signals within 24h window of any high-impact event

---

## Alert Output Format

Every signal serialises to:

```
BANKNIFTY BEARISH MOMENTUM SETUP

Bias: PUT INTRADAY

Reason:
• Price below VWAP (22500.00)
• RSI 38.0 — weak
• MACD negative expansion (-14.00)
• OI: SHORT_BUILDUP
• Heavy CE writing at 22500
• PCR 0.65 (BEARISH)
• VIX 19.0 — rising

Trade: Buy 22000 PE 09 May 2024 (DTE=5, Premium=185.00)
Entry: Premium breakdown below 22400.00 zone
SL: Spot reclaims VWAP or 22580.00
Targets: 22220.00 (1:1 RR) | 22040.00 (1:2 RR)
R:R: 1:2
Confidence: A-
Position Size: Standard position (1.5–2% capital)
Time Sensitivity: Avoid holding after 2:30 PM if momentum fades.
```

---

## What Was Built vs What Is Missing

### Built and working
- All 8 engine layers
- FastAPI app with REST endpoints and WebSocket live push
- APScheduler: scans every 5 min during 9:15–15:30 IST, pre-market bias report at 9:00, EOD export at 15:35
- Telegram alerts (MarkdownV2 formatted)
- JSON/CSV file export to `exports/`
- Dashboard UI at `http://localhost:8000`

### Specified but never implemented
These were in the original prompt but are missing from the codebase:

- **FinNifty and Midcap indices** — spec included these; only Nifty and BankNifty instrument tokens are hardcoded (`_NIFTY_TOKEN`, `_BANKNIFTY_TOKEN` in `screener.py`)
- **Bollinger Bands** — specified in Layer 3 but not computed in `engines/technical.py`
- **Multi-timeframe analysis** — spec calls for Daily+1H for directional bias, 5m+15m for entry, 30m for confirmation; the app uses only 5-minute candles for everything
- **Crude oil and US futures** — specified in Layer 8 macro tracking but not fetched in `engines/macro_risk.py`
- **Additional alert channels** — WhatsApp, Slack, Discord were in scope; only Telegram was built
- **Backtesting tools** — vectorbt and Backtrader were mentioned in the tech stack; not integrated

### Built but not wired up (DB layer)
The original 15-step plan included Step 14:

> *"Database & migrations: SQLAlchemy async + TimescaleDB hypertable for ohlcv_data. Alembic migrations for regime_snapshots, signals, alert_log tables."*

The models and migrations were created:
- `app/models/signal.py` → `SignalModel` (full signal payload with JSON column)
- `app/models/market_regime.py` → `RegimeSnapshotModel` (TimescaleDB hypertable planned)
- `app/models/alert.py` → `AlertLogModel` (delivery status tracking)
- `alembic/versions/001_initial_schema.py` → creates all three tables + hypertable

**But no router or service ever calls `get_db()` or writes to these tables.** Signals are stored in-memory only (`_last_signals` list in `routers/signals.py`) and are lost on server restart.

### What the DB was for
1. **Signal history** — query "show me all A+ CALL signals from last month and their outcomes"
2. **Regime snapshots** — chart how VIX and market bias evolved intraday/daily
3. **Alert log** — track whether each Telegram message was delivered successfully
4. **Backtesting foundation** — once signals are persisted, evaluate whether A+ signals outperformed B signals over time

To activate persistence: wire `get_db()` into the scan flow and `INSERT` signals after `run_scan()` returns.

---

## Scheduled Jobs

| Job | Schedule | What it does |
|---|---|---|
| `_scan_job` | Every 5 min, 9:15–15:30 IST | Full 8-layer scan → Telegram + file export + WebSocket push |
| `_daily_bias_report` | 9:00 AM IST | Pre-market regime summary to Telegram |
| `_eod_export` | 15:35 IST | EOD notification with export file paths |

---

## Data Sources

| Data | Source |
|---|---|
| OHLCV, option chain, LTP | Zerodha Kite Connect API |
| India VIX | NSE public JSON feed |
| Market breadth (A/D) | NSE public JSON feed |
| FII/DII flows | NSE `fiidiiTradeReact` endpoint |
| USD/INR | Yahoo Finance |
| Economic calendar | `data/events.json` (manually maintained) |

---

## Tech Stack

- **Backend**: Python 3.11, FastAPI, uvicorn
- **Scheduler**: APScheduler (AsyncIOScheduler)
- **Data**: Zerodha KiteConnect SDK, httpx, pandas, numpy
- **Technical indicators**: TA-Lib (optional), pure-pandas fallback
- **Alerts**: python-telegram-bot
- **DB (unused)**: SQLAlchemy async, asyncpg, TimescaleDB/PostgreSQL, Alembic
- **Caching**: cachetools TTLCache (LTP: 30s, OI: 60s, regime: 300s)
