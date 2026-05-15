# paper_trader

Automated paper trading module for the NSE F&O Screener.
Every signal auto-enters a trade, monitors it via KiteConnect WebSocket,
and exits on SL / Target / Time — all non-blocking, all logged to SQLite.

---

## File structure

```
paper_trader/
├── __init__.py          Module singleton + file logger
├── trader.py            PaperTrader class, on_signal(), threading
├── strike_picker.py     ITM strike + nearest weekly expiry
├── monitor.py           Tick-based SL/T1/T2/TIME/MARKET_CLOSE exits
├── db.py                SQLite read/write via dedicated write-queue thread
├── instruments.py       Instruments CSV download, caching, token lookup
├── schema.sql           Tables, indexes, and accuracy views
├── config.py            All constants (DB_PATH, intervals, time rules)
└── instruments_cache.csv  Auto-generated on first run
```

---

## Initialisation (3 lines added to existing screener)

```python
# ── In app/main.py, inside the lifespan() context manager (startup block):
import paper_trader
from app.data.kite_client import kite_client
paper_trader.init(kite=kite_client.kite)

# ── In app/screener.py, after `signals.append(signal)` (line ~137):
import paper_trader
paper_trader.on_signal(signal)
```

`on_signal()` returns **immediately** — it never blocks the screener loop.

---

## Strike selection logic

| Direction | Rule                              | Example (spot=973, interval=10) |
|-----------|-----------------------------------|---------------------------------|
| CALL      | First strike **below** spot (ITM) | 970 CE                          |
| PUT       | First strike **above** spot (ITM) | 980 PE                          |

- `ITM_STEPS = 1` in `config.py` — increase to go deeper ITM.
- Always selects the **nearest weekly expiry** with ≥ 1 trading day remaining.
- Strike intervals: NIFTY=50, BANKNIFTY=100, FINNIFTY=50, MIDCPNIFTY=25.
  Stocks: inferred from the instruments CSV.

---

## Exit conditions (checked on every spot tick)

| Signal | SL trigger              | T1 trigger              | T2 trigger              |
|--------|-------------------------|-------------------------|-------------------------|
| CALL   | spot falls below sl_spot | spot rises above target1 | spot rises above target2 |
| PUT    | spot rises above sl_spot | spot falls below target1 | spot falls below target2 |

Additional exits:
- **TIME**: `exit_time_rule` reached (e.g. `14:30`) — from screener's `time_sensitivity`
- **MARKET_CLOSE**: `15:25` hard limit regardless of P&L

---

## SQLite database

**File:** `screener_trades.db` (project root, configurable via `config.py`)

| Table / View            | Purpose                                                        |
|-------------------------|----------------------------------------------------------------|
| `signals`               | Every signal emitted — permanent audit trail                   |
| `trades`                | Every paper trade (or SKIPPED record if strike not found)      |
| `accuracy_overall`      | Single-row: total trades, wins, losses, win rate, avg P&L      |
| `accuracy_by_grade`     | Win rate split by grade (A+, A-, B)                            |
| `accuracy_by_confidence`| Win rate split by gate_score band (80+, 60-79, below 60)       |
| `accuracy_by_exit`      | Win rate split by exit reason (SL, T1, T2, TIME, MARKET_CLOSE) |
| `accuracy_by_vix`       | Win rate split by VIX environment (<14, 14-18, >18)            |
| `accuracy_by_htf`       | Win rate split by HTF trend (BULLISH, BEARISH, NEUTRAL)        |
| `accuracy_by_time`      | Win rate split by time of day (morning, midday, afternoon)     |
| `accuracy_by_symbol`    | Win rate per symbol+direction (min 3 trades to appear)         |

WAL journal mode is enabled — the Electron app can open the DB read-only
at any time without blocking paper trade writes.

---

## Column reference for Electron developer

### `signals` table

| Column         | Type    | Description                                              |
|----------------|---------|----------------------------------------------------------|
| id             | INTEGER | Auto-increment primary key                               |
| timestamp      | DATETIME| Signal generation time (IST)                             |
| symbol         | TEXT    | Underlying: "NIFTY", "SBIN", etc.                       |
| direction      | TEXT    | "CALL" or "PUT"                                          |
| grade          | TEXT    | Confidence tier: "A+", "A-", "B"                        |
| confidence     | INTEGER | Gate score 0–100                                         |
| entry_spot     | REAL    | Spot price at signal generation                          |
| sl_spot        | REAL    | Stop loss spot level                                     |
| vwap           | REAL    | VWAP at signal time                                      |
| target1        | REAL    | Target 1 spot level (1:1 RR)                             |
| target2        | REAL    | Target 2 spot level (1:2 RR)                             |
| rr             | TEXT    | Risk:reward ratio string, e.g. "1:2"                    |
| rsi            | REAL    | RSI value                                                |
| macd_hist      | REAL    | MACD histogram value                                     |
| vix            | REAL    | India VIX at signal time                                 |
| pcr            | REAL    | Put-Call Ratio                                           |
| oi             | TEXT    | OI interpretation: LONG_BUILDUP, SHORT_BUILDUP, etc.    |
| htf_trend      | TEXT    | Higher timeframe trend: BULLISH, BEARISH, NEUTRAL        |
| divergence     | INTEGER | RSI divergence detected: 1=yes, 0=no                    |
| active_signals | TEXT    | JSON array of gates that passed, e.g. ["rsi_divergence"] |
| position_size  | TEXT    | Sizing guidance from screener                            |
| exit_time_rule | TEXT    | "HH:MM" or NULL                                          |
| notes          | TEXT    | SKIPPED reason or error detail                           |

### `trades` table

| Column          | Type    | Description                                             |
|-----------------|---------|---------------------------------------------------------|
| id              | INTEGER | Auto-increment primary key                              |
| signal_id       | INTEGER | FK → signals.id                                         |
| symbol          | TEXT    | Underlying                                              |
| direction       | TEXT    | "CALL" or "PUT"                                         |
| strike          | REAL    | Selected option strike price                            |
| expiry          | TEXT    | "YYYY-MM-DD"                                            |
| option_type     | TEXT    | "CE" or "PE"                                            |
| instrument_token| INTEGER | Kite token for the option contract                      |
| spot_token      | INTEGER | Kite token for the underlying spot                      |
| entry_premium   | REAL    | Option LTP at entry (₹ per unit)                       |
| entry_spot      | REAL    | Underlying spot at entry                                |
| entry_time      | DATETIME| Trade entry timestamp                                   |
| exit_premium    | REAL    | Option LTP at exit                                      |
| exit_spot       | REAL    | Underlying spot at exit                                 |
| exit_time       | DATETIME| Trade exit timestamp                                    |
| exit_reason     | TEXT    | SL / T1 / T2 / TIME / MARKET_CLOSE / ERROR              |
| pnl_points      | REAL    | exit_premium − entry_premium (negative = loss)          |
| pnl_percent     | REAL    | pnl_points / entry_premium × 100                        |
| outcome         | TEXT    | WIN / LOSS / BREAKEVEN                                  |
| status          | TEXT    | WATCHING / ACTIVE / CLOSED / ERROR / SKIPPED            |
| lots            | INTEGER | Number of lots (always 1 for paper trading)             |

---

## Accuracy dashboard queries

```sql
-- Overall win rate
SELECT * FROM accuracy_overall;

-- Best-performing grade
SELECT * FROM accuracy_by_grade ORDER BY win_rate_pct DESC;

-- Best VIX environment to trade in
SELECT * FROM accuracy_by_vix ORDER BY win_rate_pct DESC;

-- Which exit reason is most profitable
SELECT * FROM accuracy_by_exit ORDER BY avg_pnl_pct DESC;

-- Best symbols (min 3 trades)
SELECT * FROM accuracy_by_symbol LIMIT 10;

-- Active trades right now
SELECT t.*, s.grade, s.vix FROM trades t
JOIN signals s ON t.signal_id = s.id
WHERE t.status IN ('WATCHING','ACTIVE')
ORDER BY t.entry_time DESC;

-- Today's closed trades
SELECT * FROM trades
WHERE status='CLOSED'
  AND date(exit_time) = date('now', '+5:30')   -- adjust for IST
ORDER BY exit_time DESC;
```

---

## Requirements (add to requirements.txt)

No new packages needed beyond what the screener already uses.
KiteConnect (`kiteconnect`) and `pandas` are already installed.
SQLite3 is part of Python's standard library.
