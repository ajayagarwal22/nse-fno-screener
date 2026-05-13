"""
PaperTrader — main class.

Threading model:
  Main thread    → on_signal() puts signal in _signal_queue (returns immediately)
  _worker thread → drains queue, resolves strike/expiry, fetches entry LTP,
                   writes to DB, registers trade with MonitorThread
  KiteTicker     → on_ticks() → MonitorThread.on_tick() checks all active trades
  DB write thread→ (inside db.py) serialises all SQLite writes

on_signal() NEVER blocks the screener.
"""
import json
import logging
import queue
import re
import threading
import time
from datetime import datetime
from typing import Optional

from paper_trader import db
from paper_trader import instruments as inst
from paper_trader.config import (
    EXIT_BEFORE_CLOSE,
    INDEX_LTP_KEYS,
    MARKET_CLOSE_HARD,
    MARKET_OPEN,
    RETRY_ATTEMPTS,
    RETRY_DELAY_S,
)
from paper_trader.monitor import ActiveTrade, MonitorThread
from paper_trader.strike_picker import pick

logger = logging.getLogger("paper_trader.trader")


# ── Signal field extraction ───────────────────────────────────────────────────

def _parse_first_price(text: str) -> float:
    """Extract the first valid price (>10) from a descriptive string."""
    for m in re.findall(r"[\d]+\.?\d*", str(text)):
        v = float(m)
        if v > 10:
            return v
    return 0.0


def _parse_time_rule(time_sensitivity: str) -> Optional[str]:
    """
    Extract HH:MM from time_sensitivity string.
    "Avoid holding after 2:30 PM..." → "14:30"
    Returns None for swing/no-rule strings.
    """
    match = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)?", str(time_sensitivity), re.IGNORECASE)
    if not match:
        return None
    h, m, meridiem = int(match.group(1)), int(match.group(2)), (match.group(3) or "").upper()
    if meridiem == "PM" and h != 12:
        h += 12
    elif meridiem == "AM" and h == 12:
        h = 0
    return f"{h:02d}:{m:02d}"


def _extract(signal) -> dict:
    """
    Map the screener's Signal dataclass → flat dict for DB insert.
    Handles both the real Signal object and plain dicts.
    """
    def g(attr, default=None):
        if isinstance(signal, dict):
            return signal.get(attr, default)
        return getattr(signal, attr, default)

    # direction
    raw_dir = g("direction", "")
    direction = raw_dir.value if hasattr(raw_dir, "value") else str(raw_dir)

    # grade (Confidence enum → string)
    raw_conf = g("confidence")
    grade = raw_conf.value if hasattr(raw_conf, "value") else str(raw_conf)

    # numeric gate score (0-100)
    gate_score = g("gate_score", 0)

    # parse price levels from descriptive strings
    entry_spot = _parse_first_price(g("entry_zone", "") or "")
    sl_spot    = _parse_first_price(g("stop_loss",  "") or "")
    target1    = _parse_first_price(g("target_1",   "") or "")
    target2    = _parse_first_price(g("target_2",   "") or "")

    # vwap: embedded in "Above VWAP (23400.50)"
    vwap_str = g("vwap_status", "") or ""
    vwap = _parse_first_price(vwap_str)

    # macd_hist: embedded in "Bullish cross (hist=+0.123)"
    macd_str  = g("macd_status", "") or ""
    macd_match = re.search(r"hist=([+-]?\d+\.?\d*)", macd_str)
    macd_hist  = float(macd_match.group(1)) if macd_match else 0.0

    # active signals: gates that passed
    gates = g("gates_passed", {}) or {}
    active_signals = json.dumps([k for k, v in gates.items() if v])

    # time exit rule
    exit_time_rule = _parse_time_rule(g("time_sensitivity", "") or "")

    ts = g("timestamp", datetime.now())
    timestamp = ts.isoformat() if isinstance(ts, datetime) else str(ts)

    return {
        "timestamp":       timestamp,
        "symbol":          g("symbol", ""),
        "direction":       direction,
        "grade":           grade,
        "confidence":      int(gate_score),
        "entry_spot":      entry_spot,
        "sl_spot":         sl_spot,
        "vwap":            vwap,
        "target1":         target1,
        "target2":         target2,
        "rr":              g("rr_ratio", ""),
        "rsi":             float(g("rsi_value", 0) or 0),
        "macd_hist":       macd_hist,
        "vix":             float(g("vix_level", 0) or 0),
        "pcr":             float(g("pcr_value", 0) or 0),
        "oi":              g("oi_interpretation", ""),
        "htf_trend":       g("htf_trend", ""),
        "divergence":      1 if g("divergence_detected", False) else 0,
        "active_signals":  active_signals,
        "position_size":   g("position_sizing", ""),
        "exit_time_rule":  exit_time_rule,
        "notes":           None,
    }


# ── PaperTrader ───────────────────────────────────────────────────────────────

class PaperTrader:
    """
    Receives screener signals and executes paper trades non-blocking.

    Usage:
        pt = PaperTrader(kite=kite_instance)
        pt.on_signal(signal)   # returns immediately every time
    """

    def __init__(self, kite):
        self._kite    = kite
        self._queue:  queue.Queue = queue.Queue()
        self._monitor = MonitorThread()
        self._ticker  = None
        self._subscribed_tokens: set[int] = set()
        self._ticker_lock = threading.Lock()

        db.init()
        inst.load(kite)

        self._worker = threading.Thread(
            target=self._process_loop, daemon=True, name="paper-trader-worker"
        )
        self._worker.start()

        self._start_ticker()
        logger.info("[PaperTrader] Initialised and ready")

    # ── Public API ────────────────────────────────────────────────────────────

    def on_signal(self, signal) -> None:
        """Non-blocking entry point. Returns immediately."""
        now_hm = datetime.now().strftime("%H:%M")
        if now_hm < MARKET_OPEN or now_hm >= MARKET_CLOSE_HARD:
            sym = getattr(signal, "symbol", "?")
            logger.info(f"[PaperTrader] Market closed — discarding signal for {sym}")
            return
        self._queue.put(signal)

    # ── Worker thread ─────────────────────────────────────────────────────────

    def _process_loop(self):
        while True:
            try:
                signal = self._queue.get(timeout=1)
                self._handle(signal)
            except queue.Empty:
                continue
            except Exception as exc:
                logger.error(f"[PaperTrader] Worker error: {exc}", exc_info=True)

    def _handle(self, signal):
        fields = _extract(signal)
        symbol    = fields["symbol"]
        direction = fields["direction"]
        sl_spot   = fields["sl_spot"]
        target1   = fields["target1"]
        target2   = fields["target2"]
        exit_rule = fields["exit_time_rule"]

        # 1. Write signal to DB first (always — even if trade fails)
        signal_id = db.insert_signal(fields)
        if not signal_id:
            logger.error(f"[PaperTrader] DB insert failed for signal {symbol}")
            return
        logger.info(f"[PaperTrader] signal#{signal_id} {symbol} {direction}")

        # 2. Refresh instruments if needed (cached; cheap after first load)
        try:
            inst.load(self._kite)
        except Exception as exc:
            logger.warning(f"[PaperTrader] Instruments refresh skipped: {exc}")

        # 3. Get live spot price
        spot = self._get_spot_ltp(symbol)
        if not spot or spot <= 0:
            self._skip_trade(signal_id, symbol, direction, "Could not fetch spot LTP")
            return

        # 4. Pick ITM strike
        result = pick(symbol, spot, direction)
        if result is None:
            self._skip_trade(signal_id, symbol, direction, "Strike/expiry not found in instruments")
            return
        strike, option_type, expiry, option_token = result

        # 5. Get entry option premium
        entry_premium = self._get_option_ltp(option_token) or 0.0

        # 6. Resolve spot instrument token for WebSocket monitoring
        spot_token = inst.get_nse_spot_token(symbol, self._kite)
        if not spot_token:
            self._skip_trade(signal_id, symbol, direction, "Could not resolve spot token")
            return

        # 7. Write trade to DB
        trade_id = db.insert_trade({
            "signal_id":        signal_id,
            "symbol":           symbol,
            "direction":        direction,
            "strike":           strike,
            "expiry":           str(expiry),
            "option_type":      option_type,
            "instrument_token": option_token,
            "spot_token":       spot_token,
            "entry_premium":    entry_premium,
            "entry_spot":       round(spot, 2),
            "entry_time":       datetime.now().isoformat(),
            "status":           "ACTIVE",
            "lots":             1,
        })
        if not trade_id:
            logger.error(f"[PaperTrader] Trade DB insert failed for signal#{signal_id}")
            return

        # 8. Register with monitor and subscribe tokens
        active = ActiveTrade(
            trade_id=trade_id,
            signal_id=signal_id,
            symbol=symbol,
            direction=direction,
            strike=strike,
            expiry=str(expiry),
            option_type=option_type,
            spot_token=spot_token,
            option_token=option_token,
            entry_premium=entry_premium,
            entry_spot=round(spot, 2),
            sl_spot=sl_spot,
            target1=target1,
            target2=target2,
            exit_time_rule=exit_rule,
        )
        self._monitor.add_trade(active)
        self._subscribe({spot_token, option_token})

        logger.info(
            f"[PaperTrader] trade#{trade_id} ENTERED "
            f"{symbol} {strike}{option_type} exp={expiry} "
            f"prem={entry_premium:.2f} spot={spot:.2f}"
        )

    def _skip_trade(self, signal_id: int, symbol: str, direction: str, reason: str):
        logger.warning(f"[PaperTrader] SKIPPED {symbol} {direction} — {reason}")
        db.insert_trade({
            "signal_id":        signal_id,
            "symbol":           symbol,
            "direction":        direction,
            "strike":           0,
            "expiry":           "",
            "option_type":      "CE" if direction == "CALL" else "PE",
            "instrument_token": None,
            "spot_token":       None,
            "entry_premium":    None,
            "entry_spot":       None,
            "entry_time":       None,
            "status":           "SKIPPED",
            "lots":             1,
        })

    # ── KiteTicker ────────────────────────────────────────────────────────────

    def restart_ticker(self, kite):
        """Restart KiteTicker with a freshly authenticated kite instance."""
        self._kite = kite
        if self._ticker:
            try:
                self._ticker.close()
            except Exception:
                pass
            self._ticker = None
        with self._ticker_lock:
            self._subscribed_tokens.clear()
        self._start_ticker()
        logger.info("[PaperTrader] KiteTicker restarted with new token")

    def _start_ticker(self):
        try:
            from kiteconnect import KiteTicker
            self._ticker = KiteTicker(
                self._kite.api_key,
                self._kite.access_token,
            )
            self._ticker.on_ticks     = self._on_ticks
            self._ticker.on_connect   = self._on_connect
            self._ticker.on_close     = self._on_close
            self._ticker.on_error     = self._on_error
            self._ticker.on_reconnect = self._on_reconnect

            t = threading.Thread(
                target=self._ticker.connect,
                kwargs={"threaded": True},
                daemon=True,
                name="paper-kite-ticker",
            )
            t.start()
            logger.info("[PaperTrader] KiteTicker started")
        except Exception as exc:
            logger.error(f"[PaperTrader] KiteTicker failed to start: {exc}")
            self._ticker = None

    def _on_ticks(self, ws, ticks):
        self._monitor.on_tick(ticks)

    def _on_connect(self, ws, response):
        logger.info("[PaperTrader] WebSocket connected")
        tokens = list(self._monitor.active_tokens())
        if tokens:
            ws.subscribe(tokens)
            ws.set_mode(ws.MODE_LTP, tokens)

    def _on_close(self, ws, code, reason):
        logger.warning(f"[PaperTrader] WebSocket closed: {code} {reason}")

    def _on_error(self, ws, code, reason):
        logger.error(f"[PaperTrader] WebSocket error: {code} {reason}")

    def _on_reconnect(self, ws, attempts):
        logger.info(f"[PaperTrader] WebSocket reconnecting (attempt {attempts})")
        if attempts >= 3:
            # Fallback: close all active trades via REST LTP to avoid zombie trades
            try:
                self._monitor.force_close_all(self._kite)
            except Exception as exc:
                logger.error(f"[PaperTrader] force_close_all failed: {exc}")

    def _subscribe(self, tokens: set[int]):
        with self._ticker_lock:
            new = tokens - self._subscribed_tokens
            if not new or self._ticker is None:
                return
            self._subscribed_tokens |= new
            try:
                self._ticker.subscribe(list(new))
                self._ticker.set_mode(self._ticker.MODE_LTP, list(new))
                logger.debug(f"[PaperTrader] Subscribed tokens: {new}")
            except Exception as exc:
                logger.error(f"[PaperTrader] Subscribe error: {exc}")

    # ── LTP helpers ───────────────────────────────────────────────────────────

    def _get_spot_ltp(self, symbol: str) -> Optional[float]:
        key = INDEX_LTP_KEYS.get(symbol, f"NSE:{symbol}")
        for attempt in range(RETRY_ATTEMPTS):
            try:
                data = self._kite.ltp([key])
                price = (data.get(key) or {}).get("last_price")
                return float(price) if price else None
            except Exception as exc:
                logger.warning(
                    f"[PaperTrader] Spot LTP attempt {attempt+1}/{RETRY_ATTEMPTS} "
                    f"{symbol}: {exc}"
                )
                if attempt < RETRY_ATTEMPTS - 1:
                    time.sleep(RETRY_DELAY_S)
        return None

    def _get_option_ltp(self, token: int) -> Optional[float]:
        for attempt in range(RETRY_ATTEMPTS):
            try:
                data = self._kite.ltp([token])
                for val in data.values():
                    price = val.get("last_price")
                    return float(price) if price else None
            except Exception as exc:
                logger.warning(
                    f"[PaperTrader] Option LTP attempt {attempt+1}/{RETRY_ATTEMPTS} "
                    f"token={token}: {exc}"
                )
                if attempt < RETRY_ATTEMPTS - 1:
                    time.sleep(RETRY_DELAY_S)
        return None
