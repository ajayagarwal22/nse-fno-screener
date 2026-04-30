# NSE F&O Options Screener

Professional-grade 8-layer NSE F&O options screener. Answers: *"Is the probability-adjusted risk-to-reward favorable RIGHT NOW?"*

Only emits a signal when all 8 layers agree — designed to be rare and high-conviction, not noisy.

---

## How to run every morning

Double-click **`Open Screener.command`** in the project folder.

It will:
1. Open the Zerodha login page in your browser
2. Enter your Zerodha **user ID** and **password**
3. Enter the **6-digit TOTP code** from your Zerodha authenticator app
4. Once logged in, the token is saved automatically and the server starts
5. Your dashboard opens at **http://localhost:8000**

That's it. No Docker, no database needed.

---

## Architecture — the 8 layers

A signal is only emitted when every layer passes. Each layer gates the next.

| Layer | Engine | Purpose |
|---|---|---|
| 1 | Market Regime | Classifies overall market state: Trending Bullish/Bearish, Rangebound, High Vol Expansion, Event Risk, Theta Decay. All downstream layers gate on this. |
| 2 | Stock Selection | Filters the full NSE F&O universe by relative strength vs Nifty, volume expansion, option chain liquidity, and OI quality. |
| 3 | Technical Confluence | Scores VWAP, EMA 20/50/200, RSI, MACD, ATR, SuperTrend. Indicators are never used in isolation — only a combined confluence score passes. |
| 4 | Derivatives Intelligence | Reads PCR, Max Pain, OI buildup type (Long Buildup / Short Buildup / Short Covering / Long Unwinding), IV skew, and call/put writing concentration. |
| 5 | Option Selection | Picks the optimal strike and expiry for intraday vs swing, based on ATM distance, IV regime, and liquidity thresholds. |
| 6 | Entry Trigger | Aggregates all 5 layers. Only fires if all gates pass. Grades the signal A+, A-, or B based on how many weighted conditions align. |
| 7 | Exit Engine | Computes stop loss from spot price structure (never from option premium), T1, T2, trailing stop, and time-based exit. |
| 8 | Macro Risk | Suppresses all signals near high-impact events (RBI, Fed, budget, expiry), when FII is heavily selling, or when USD/INR is stressed. |

---

## Dashboard

The dashboard at **http://localhost:8000** shows:

- **Market Regime** — current classification and overall bias
- **VIX & Environment** — VIX level, whether conditions favour calls or puts
- **Market Breadth** — Advance/Decline ratio
- **Macro Risk** — High/Low risk flag, upcoming events, FII flow, USD/INR
- **Signals table** — active signals with Strike, Expiry, Premium, Entry, Stop Loss, T1, T2, R:R
- **Scan Now** button — triggers a fresh scan on demand
- **Live dot** — green when WebSocket is connected; dashboard auto-updates on each scan

---

## What was not built (planned but incomplete)

The codebase has DB models (`app/models/`) and Alembic migrations that were designed but never wired up:

- **Signal history** — every signal was meant to be persisted to TimescaleDB so you could query historical performance
- **Regime snapshots** — the market regime was meant to be logged every scan for charting how VIX and bias evolved
- **Backtesting** — storing signals historically would eventually enable checking whether A+ signals actually worked

Currently all signals are in-memory only (lost on server restart). The DB layer exists but no router writes to it.

---

## First-time setup

```bash
# Install dependencies
pip3 install fastapi "uvicorn[standard]" pydantic-settings python-dotenv kiteconnect \
    pandas numpy httpx "SQLAlchemy[asyncio]" asyncpg alembic APScheduler \
    python-telegram-bot cachetools pytest pytest-asyncio pytest-mock
```

Keys go in `.env` (already configured).

---

## API endpoints

| Endpoint | Description |
|---|---|
| `GET /` | Dashboard |
| `GET /market/regime` | Market regime and bias |
| `GET /market/breadth` | Advance/Decline data |
| `GET /market/macro-risk` | Macro risk assessment |
| `POST /scan?trade_type=INTRADAY` | Trigger a scan |
| `GET /signals` | Current signals (in-memory) |
| `GET /docs` | Interactive API docs |
| `ws://localhost:8000/ws/alerts` | WebSocket live signal feed |
