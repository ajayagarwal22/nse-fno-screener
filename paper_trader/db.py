"""
All SQLite read/write operations for the paper trader.

Architecture:
  - Single dedicated DBWriteThread with a queue — prevents SQLite
    "database is locked" errors from concurrent writers.
  - WAL journal mode lets the Electron app open the DB read-only
    at any time without blocking paper trade writes.
  - Every write is an atomic transaction; rollback on any error.
"""
import logging
import queue
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable, Optional

from paper_trader.config import DB_PATH

logger = logging.getLogger("paper_trader.db")

# ── Internal write queue ──────────────────────────────────────────────────────
_write_queue: queue.Queue = queue.Queue()
_shutdown     = threading.Event()
_db_thread: Optional[threading.Thread] = None


# ── Connection helper ─────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")   # safe + fast with WAL
    return conn


# ── Schema ────────────────────────────────────────────────────────────────────

def init():
    """Create all tables and views. Safe to call on every startup (IF NOT EXISTS)."""
    schema = (Path(__file__).parent / "schema.sql").read_text()
    conn = _connect()
    try:
        conn.executescript(schema)
        conn.commit()
        logger.info(f"[DB] Initialised at {DB_PATH}")
    finally:
        conn.close()
    _start_write_thread()


# ── Write thread ──────────────────────────────────────────────────────────────

def _start_write_thread():
    global _db_thread
    if _db_thread and _db_thread.is_alive():
        return
    _db_thread = threading.Thread(
        target=_write_loop, daemon=True, name="paper-db-writer"
    )
    _db_thread.start()
    logger.debug("[DB] Write thread started")


def _write_loop():
    conn = _connect()
    while not _shutdown.is_set():
        try:
            fn, args, result_box, done_event = _write_queue.get(timeout=0.5)
            try:
                result = fn(conn, *args)
                conn.commit()
                result_box.append(result)
            except Exception as exc:
                conn.rollback()
                logger.error(f"[DB] Write error: {exc}", exc_info=True)
                result_box.append(None)
            finally:
                if done_event:
                    done_event.set()
                _write_queue.task_done()
        except queue.Empty:
            continue
    conn.close()
    logger.debug("[DB] Write thread stopped")


def _enqueue(fn: Callable, *args, wait: bool = False) -> Any:
    """Submit fn to the DB write thread. If wait=True, block until done."""
    result_box: list = []
    done_event = threading.Event() if wait else None
    _write_queue.put((fn, args, result_box, done_event))
    if wait and done_event:
        done_event.wait(timeout=10)
        return result_box[0] if result_box else None


def shutdown():
    _shutdown.set()
    if _db_thread:
        _db_thread.join(timeout=5)


# ── Raw write functions (executed inside DB thread) ───────────────────────────

def _do_insert_signal(conn: sqlite3.Connection, data: dict) -> int:
    cur = conn.execute("""
        INSERT INTO signals (
            timestamp, symbol, direction, grade, confidence,
            entry_spot, sl_spot, vwap, target1, target2, rr,
            rsi, macd_hist, vix, pcr, oi, htf_trend, divergence,
            active_signals, position_size, exit_time_rule, notes
        ) VALUES (
            :timestamp, :symbol, :direction, :grade, :confidence,
            :entry_spot, :sl_spot, :vwap, :target1, :target2, :rr,
            :rsi, :macd_hist, :vix, :pcr, :oi, :htf_trend, :divergence,
            :active_signals, :position_size, :exit_time_rule, :notes
        )
    """, data)
    return cur.lastrowid


def _do_insert_trade(conn: sqlite3.Connection, data: dict) -> int:
    cur = conn.execute("""
        INSERT INTO trades (
            signal_id, symbol, direction, strike, expiry,
            option_type, instrument_token, spot_token,
            entry_premium, entry_spot, entry_time, status, lots
        ) VALUES (
            :signal_id, :symbol, :direction, :strike, :expiry,
            :option_type, :instrument_token, :spot_token,
            :entry_premium, :entry_spot, :entry_time, :status, :lots
        )
    """, data)
    return cur.lastrowid


def _do_close_trade(conn: sqlite3.Connection, trade_id: int, data: dict):
    conn.execute("""
        UPDATE trades SET
            exit_premium = :exit_premium,
            exit_spot    = :exit_spot,
            exit_time    = :exit_time,
            exit_reason  = :exit_reason,
            pnl_points   = :pnl_points,
            pnl_percent  = :pnl_percent,
            outcome      = :outcome,
            status       = 'CLOSED'
        WHERE id = :trade_id
    """, {**data, "trade_id": trade_id})


def _do_confirm_entry(conn: sqlite3.Connection, trade_id: int, data: dict):
    conn.execute("""
        UPDATE trades SET
            entry_premium = :entry_premium,
            entry_spot    = :entry_spot,
            entry_time    = :entry_time,
            status        = 'ACTIVE'
        WHERE id = :trade_id
    """, {**data, "trade_id": trade_id})


def _do_set_status(conn: sqlite3.Connection, trade_id: int, status: str):
    conn.execute("UPDATE trades SET status=? WHERE id=?", (status, trade_id))


# ── Public thread-safe API ────────────────────────────────────────────────────

def insert_signal(data: dict) -> Optional[int]:
    """Insert a signal row. Blocks until written. Returns new signal_id."""
    return _enqueue(_do_insert_signal, data, wait=True)


def insert_trade(data: dict) -> Optional[int]:
    """Insert a trade row. Blocks until written. Returns new trade_id."""
    return _enqueue(_do_insert_trade, data, wait=True)


def close_trade(trade_id: int, data: dict):
    """Write exit data for a trade (fire-and-forget — non-blocking)."""
    _enqueue(_do_close_trade, trade_id, data)


def confirm_entry(trade_id: int, data: dict):
    """Write confirmed entry details and flip status to ACTIVE (fire-and-forget)."""
    _enqueue(_do_confirm_entry, trade_id, data)


def set_trade_status(trade_id: int, status: str):
    """Update trade status (fire-and-forget)."""
    _enqueue(_do_set_status, trade_id, status)


def get_active_trades() -> list:
    """
    Read all WATCHING/ACTIVE trades. Uses its own read connection
    (separate from write thread) — safe under WAL.
    """
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT * FROM trades WHERE status IN ('WATCHING','ACTIVE')"
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()
