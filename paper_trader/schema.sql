-- ================================================================
-- Paper Trader SQLite Schema
-- File: screener_trades.db  (project root, configurable in config.py)
-- Shared with Electron app via WAL mode (readers never block writers)
-- ================================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ----------------------------------------------------------------
-- TABLE: signals
-- One row per signal emitted by the screener.
-- Never deleted — permanent audit trail.
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       DATETIME NOT NULL,          -- signal generation time (IST)
    symbol          TEXT NOT NULL,              -- e.g. "SBIN", "NIFTY"
    direction       TEXT NOT NULL               -- "CALL" or "PUT"
                    CHECK(direction IN ('CALL','PUT')),
    grade           TEXT,                       -- "A+", "A-", "B"  (confidence tier)
    confidence      INTEGER,                    -- gate_score 0-100
    entry_spot      REAL,                       -- spot price at signal time
    sl_spot         REAL,                       -- stop loss spot level
    vwap            REAL,                       -- VWAP at signal time
    target1         REAL,                       -- Target 1 spot level (1:1 RR)
    target2         REAL,                       -- Target 2 spot level (1:2 RR)
    rr              TEXT,                       -- e.g. "1:2"
    rsi             REAL,                       -- RSI value at signal
    macd_hist       REAL,                       -- MACD histogram value
    vix             REAL,                       -- India VIX at signal time
    pcr             REAL,                       -- Put-Call Ratio
    oi              TEXT,                       -- OI interpretation: LONG_BUILDUP etc.
    htf_trend       TEXT,                       -- Higher timeframe trend: BULLISH/BEARISH
    divergence      INTEGER DEFAULT 0           -- RSI divergence detected: 0 or 1
                    CHECK(divergence IN (0,1)),
    active_signals  TEXT,                       -- JSON array: gate names that passed
    position_size   TEXT,                       -- e.g. "Full position (2–3% capital)"
    exit_time_rule  TEXT,                       -- "14:30" or NULL for swing
    notes           TEXT,                       -- SKIPPED reason or error detail
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ----------------------------------------------------------------
-- TABLE: trades
-- One row per paper trade. Each signal produces exactly one trade
-- (or a SKIPPED record if instrument resolution fails).
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trades (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id        INTEGER REFERENCES signals(id),
    symbol           TEXT NOT NULL,
    direction        TEXT NOT NULL               -- "CALL" or "PUT"
                     CHECK(direction IN ('CALL','PUT')),
    strike           REAL NOT NULL,              -- selected option strike price
    expiry           TEXT NOT NULL,              -- "YYYY-MM-DD"
    option_type      TEXT NOT NULL               -- "CE" or "PE"
                     CHECK(option_type IN ('CE','PE')),
    instrument_token INTEGER,                    -- Kite instrument token for the option
    spot_token       INTEGER,                    -- Kite instrument token for the underlying
    entry_premium    REAL,                       -- option LTP at entry (₹ per unit)
    entry_spot       REAL,                       -- underlying spot at entry
    entry_time       DATETIME,
    exit_premium     REAL,                       -- option LTP at exit
    exit_spot        REAL,                       -- underlying spot at exit
    exit_time        DATETIME,
    exit_reason      TEXT                        -- SL / T1 / T2 / TIME / MARKET_CLOSE / ERROR
                     CHECK(exit_reason IN ('SL','T1','T2','TIME','MARKET_CLOSE','ERROR',NULL)),
    pnl_points       REAL,                       -- exit_premium - entry_premium per unit
    pnl_rupees       REAL,                       -- pnl_points * lot_size (actual money)
    pnl_percent      REAL,                       -- pnl_points / entry_premium * 100
    outcome          TEXT                        -- WIN / LOSS / BREAKEVEN
                     CHECK(outcome IN ('WIN','LOSS','BREAKEVEN',NULL)),
    status           TEXT NOT NULL DEFAULT 'WATCHING'
                     CHECK(status IN ('WATCHING','ACTIVE','CLOSED','ERROR','SKIPPED')),
    lots             INTEGER DEFAULT 1,
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ----------------------------------------------------------------
-- INDEXES — keep Electron queries fast
-- ----------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_signals_timestamp  ON signals(timestamp);
CREATE INDEX IF NOT EXISTS idx_signals_symbol     ON signals(symbol);
CREATE INDEX IF NOT EXISTS idx_signals_grade      ON signals(grade);
CREATE INDEX IF NOT EXISTS idx_trades_signal_id   ON trades(signal_id);
CREATE INDEX IF NOT EXISTS idx_trades_status      ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_symbol      ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_entry_time  ON trades(entry_time);
CREATE INDEX IF NOT EXISTS idx_trades_outcome     ON trades(outcome);

-- ================================================================
-- ACCURACY VIEWS
-- Each view is a GROUP BY slice — Electron queries these directly.
-- All views only count status='CLOSED' trades.
-- ================================================================

-- Overall summary (single row)
CREATE VIEW IF NOT EXISTS accuracy_overall AS
SELECT
    COUNT(*)                                                                    AS total_trades,
    SUM(CASE WHEN t.outcome = 'WIN'  THEN 1 ELSE 0 END)                        AS wins,
    SUM(CASE WHEN t.outcome = 'LOSS' THEN 1 ELSE 0 END)                        AS losses,
    ROUND(100.0 * SUM(CASE WHEN t.outcome = 'WIN' THEN 1 ELSE 0 END)
          / MAX(COUNT(*), 1), 2)                                                AS win_rate_pct,
    ROUND(AVG(t.pnl_percent), 2)                                               AS avg_pnl_pct,
    ROUND(AVG(CASE WHEN t.outcome = 'WIN'  THEN t.pnl_percent END), 2)         AS avg_win_pct,
    ROUND(AVG(CASE WHEN t.outcome = 'LOSS' THEN t.pnl_percent END), 2)         AS avg_loss_pct,
    ROUND(SUM(t.pnl_percent), 2)                                               AS total_pnl_pct
FROM trades t
WHERE t.status = 'CLOSED';

-- By grade (A+, A-, B)
CREATE VIEW IF NOT EXISTS accuracy_by_grade AS
SELECT
    COALESCE(s.grade, 'unknown')                                               AS grade,
    COUNT(*)                                                                    AS trades,
    SUM(CASE WHEN t.outcome = 'WIN' THEN 1 ELSE 0 END)                        AS wins,
    ROUND(100.0 * SUM(CASE WHEN t.outcome = 'WIN' THEN 1 ELSE 0 END)
          / COUNT(*), 2)                                                        AS win_rate_pct,
    ROUND(AVG(t.pnl_percent), 2)                                               AS avg_pnl_pct
FROM trades t
LEFT JOIN signals s ON t.signal_id = s.id
WHERE t.status = 'CLOSED'
GROUP BY s.grade
ORDER BY win_rate_pct DESC;

-- By confidence band (gate_score)
CREATE VIEW IF NOT EXISTS accuracy_by_confidence AS
SELECT
    CASE
        WHEN s.confidence >= 80 THEN '80+ (high)'
        WHEN s.confidence >= 60 THEN '60-79 (medium)'
        ELSE 'below 60 (low)'
    END                                                                         AS confidence_band,
    COUNT(*)                                                                    AS trades,
    SUM(CASE WHEN t.outcome = 'WIN' THEN 1 ELSE 0 END)                        AS wins,
    ROUND(100.0 * SUM(CASE WHEN t.outcome = 'WIN' THEN 1 ELSE 0 END)
          / COUNT(*), 2)                                                        AS win_rate_pct,
    ROUND(AVG(t.pnl_percent), 2)                                               AS avg_pnl_pct
FROM trades t
LEFT JOIN signals s ON t.signal_id = s.id
WHERE t.status = 'CLOSED'
GROUP BY confidence_band
ORDER BY win_rate_pct DESC;

-- By exit reason (which exit type wins most)
CREATE VIEW IF NOT EXISTS accuracy_by_exit AS
SELECT
    COALESCE(t.exit_reason, 'unknown')                                         AS exit_reason,
    COUNT(*)                                                                    AS trades,
    SUM(CASE WHEN t.outcome = 'WIN' THEN 1 ELSE 0 END)                        AS wins,
    ROUND(100.0 * SUM(CASE WHEN t.outcome = 'WIN' THEN 1 ELSE 0 END)
          / COUNT(*), 2)                                                        AS win_rate_pct,
    ROUND(AVG(t.pnl_percent), 2)                                               AS avg_pnl_pct
FROM trades t
WHERE t.status = 'CLOSED'
GROUP BY t.exit_reason
ORDER BY trades DESC;

-- By VIX environment
CREATE VIEW IF NOT EXISTS accuracy_by_vix AS
SELECT
    CASE
        WHEN s.vix < 14  THEN 'low VIX (<14)'
        WHEN s.vix <= 18 THEN 'normal VIX (14-18)'
        ELSE 'high VIX (>18)'
    END                                                                         AS vix_band,
    COUNT(*)                                                                    AS trades,
    SUM(CASE WHEN t.outcome = 'WIN' THEN 1 ELSE 0 END)                        AS wins,
    ROUND(100.0 * SUM(CASE WHEN t.outcome = 'WIN' THEN 1 ELSE 0 END)
          / COUNT(*), 2)                                                        AS win_rate_pct,
    ROUND(AVG(t.pnl_percent), 2)                                               AS avg_pnl_pct
FROM trades t
LEFT JOIN signals s ON t.signal_id = s.id
WHERE t.status = 'CLOSED'
GROUP BY vix_band
ORDER BY win_rate_pct DESC;

-- By HTF trend alignment
CREATE VIEW IF NOT EXISTS accuracy_by_htf AS
SELECT
    COALESCE(s.htf_trend, 'unknown')                                           AS htf_trend,
    COUNT(*)                                                                    AS trades,
    SUM(CASE WHEN t.outcome = 'WIN' THEN 1 ELSE 0 END)                        AS wins,
    ROUND(100.0 * SUM(CASE WHEN t.outcome = 'WIN' THEN 1 ELSE 0 END)
          / COUNT(*), 2)                                                        AS win_rate_pct,
    ROUND(AVG(t.pnl_percent), 2)                                               AS avg_pnl_pct
FROM trades t
LEFT JOIN signals s ON t.signal_id = s.id
WHERE t.status = 'CLOSED'
GROUP BY s.htf_trend
ORDER BY win_rate_pct DESC;

-- By time of day (when was the signal generated)
CREATE VIEW IF NOT EXISTS accuracy_by_time AS
SELECT
    CASE
        WHEN strftime('%H:%M', t.entry_time) < '11:00' THEN 'morning (9:15-11:00)'
        WHEN strftime('%H:%M', t.entry_time) < '13:00' THEN 'midday (11:00-13:00)'
        ELSE 'afternoon (13:00-14:30)'
    END                                                                         AS time_of_day,
    COUNT(*)                                                                    AS trades,
    SUM(CASE WHEN t.outcome = 'WIN' THEN 1 ELSE 0 END)                        AS wins,
    ROUND(100.0 * SUM(CASE WHEN t.outcome = 'WIN' THEN 1 ELSE 0 END)
          / COUNT(*), 2)                                                        AS win_rate_pct,
    ROUND(AVG(t.pnl_percent), 2)                                               AS avg_pnl_pct
FROM trades t
WHERE t.status = 'CLOSED'
GROUP BY time_of_day
ORDER BY win_rate_pct DESC;

-- By symbol (which underlyings perform best)
CREATE VIEW IF NOT EXISTS accuracy_by_symbol AS
SELECT
    t.symbol,
    t.direction,
    COUNT(*)                                                                    AS trades,
    SUM(CASE WHEN t.outcome = 'WIN' THEN 1 ELSE 0 END)                        AS wins,
    ROUND(100.0 * SUM(CASE WHEN t.outcome = 'WIN' THEN 1 ELSE 0 END)
          / COUNT(*), 2)                                                        AS win_rate_pct,
    ROUND(AVG(t.pnl_percent), 2)                                               AS avg_pnl_pct
FROM trades t
WHERE t.status = 'CLOSED'
GROUP BY t.symbol, t.direction
HAVING COUNT(*) >= 3
ORDER BY win_rate_pct DESC;
