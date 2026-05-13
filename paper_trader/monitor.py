"""
MonitorThread — single thread that checks ALL active trades on every tick.

Design:
  - One thread manages all trades (not one thread per trade).
  - KiteTicker callback delivers ticks → on_tick() checks every active trade.
  - Thread-safe: _trades dict protected by RLock.
  - All DB writes go through db.close_trade() (fire-and-forget).

Exit conditions checked on every SPOT tick:
  PUT:  SL = spot > sl_spot  |  T1 = spot < target1  |  T2 = spot < target2
  CALL: SL = spot < sl_spot  |  T1 = spot > target1  |  T2 = spot > target2
  TIME: current time >= exit_time_rule
  MARKET_CLOSE: current time >= EXIT_BEFORE_CLOSE
"""
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from paper_trader import db
from paper_trader.config import EXIT_BEFORE_CLOSE

logger = logging.getLogger("paper_trader.monitor")


@dataclass
class ActiveTrade:
    trade_id:       int
    signal_id:      int
    symbol:         str
    direction:      str         # "CALL" or "PUT"
    strike:         float
    expiry:         str
    option_type:    str         # "CE" or "PE"
    spot_token:     int
    option_token:   int
    entry_premium:  float
    entry_spot:     float
    sl_spot:        float
    target1:        float
    target2:        float
    exit_time_rule: Optional[str] = None   # "14:30" or None


class MonitorThread:
    def __init__(self):
        self._trades: dict[int, ActiveTrade] = {}  # trade_id → ActiveTrade
        self._ltp:    dict[int, float]        = {}  # token    → last price
        self._lock    = threading.RLock()

    # ── Trade registry ────────────────────────────────────────────────────────

    def add_trade(self, trade: ActiveTrade):
        with self._lock:
            self._trades[trade.trade_id] = trade
        logger.info(
            f"[Monitor] +trade#{trade.trade_id} "
            f"{trade.symbol} {trade.direction} {trade.strike}{trade.option_type} "
            f"exp={trade.expiry} entry_prem={trade.entry_premium:.2f}"
        )

    def remove_trade(self, trade_id: int):
        with self._lock:
            self._trades.pop(trade_id, None)

    def active_tokens(self) -> set[int]:
        """All instrument tokens currently being watched."""
        with self._lock:
            tokens: set[int] = set()
            for t in self._trades.values():
                tokens.add(t.spot_token)
                tokens.add(t.option_token)
            return tokens

    def has_active_trades(self) -> bool:
        with self._lock:
            return bool(self._trades)

    # ── Tick handler (called from KiteTicker thread) ──────────────────────────

    def on_tick(self, ticks: list):
        """
        Process a batch of ticks from KiteTicker.
        Updates LTP cache and checks all active trades for exit conditions.
        """
        with self._lock:
            # 1. Update LTP cache
            for tick in ticks:
                token = tick.get("instrument_token")
                price = tick.get("last_price") or tick.get("ltp")
                if token and price:
                    self._ltp[token] = float(price)

            # 2. Check every active trade
            now     = datetime.now()
            now_hm  = now.strftime("%H:%M")
            to_exit: list[tuple[int, str]] = []   # (trade_id, reason)

            for trade_id, trade in self._trades.items():
                spot    = self._ltp.get(trade.spot_token)
                opt_ltp = self._ltp.get(trade.option_token)

                if spot is None:
                    continue  # no tick for this underlying yet

                reason = self._check_exit(trade, spot, now_hm)
                if reason:
                    to_exit.append((trade_id, reason, spot, opt_ltp or 0.0))

            # 3. Process exits (outside inner loop to avoid dict mutation)
            for trade_id, reason, exit_spot, exit_premium in to_exit:
                trade = self._trades.pop(trade_id, None)
                if trade is None:
                    continue
                self._record_exit(trade, reason, exit_spot, exit_premium, now)

    def _check_exit(
        self,
        trade: ActiveTrade,
        spot: float,
        now_hm: str,
    ) -> Optional[str]:
        """Return exit reason string or None."""
        # Time-rule exit
        if trade.exit_time_rule and now_hm >= trade.exit_time_rule:
            return "TIME"
        # Market-close safety net
        if now_hm >= EXIT_BEFORE_CLOSE:
            return "MARKET_CLOSE"

        if trade.direction == "PUT":
            if spot >= trade.sl_spot:
                return "SL"
            if trade.target2 and spot <= trade.target2:
                return "T2"
            if trade.target1 and spot <= trade.target1:
                return "T1"
        else:  # CALL
            if spot <= trade.sl_spot:
                return "SL"
            if trade.target2 and spot >= trade.target2:
                return "T2"
            if trade.target1 and spot >= trade.target1:
                return "T1"
        return None

    def _record_exit(
        self,
        trade: ActiveTrade,
        reason: str,
        exit_spot: float,
        exit_premium: float,
        now: datetime,
    ):
        pnl        = exit_premium - trade.entry_premium
        pnl_pct    = (pnl / trade.entry_premium * 100) if trade.entry_premium else 0.0
        outcome    = "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BREAKEVEN")

        db.close_trade(trade.trade_id, {
            "exit_premium": round(exit_premium, 2),
            "exit_spot":    round(exit_spot,    2),
            "exit_time":    now.isoformat(),
            "exit_reason":  reason,
            "pnl_points":   round(pnl,     2),
            "pnl_percent":  round(pnl_pct, 2),
            "outcome":      outcome,
        })
        logger.info(
            f"[Monitor] CLOSED trade#{trade.trade_id} {trade.symbol} "
            f"{trade.strike}{trade.option_type} | {reason} | "
            f"pnl={pnl:+.2f} ({pnl_pct:+.1f}%) | {outcome}"
        )

    # ── Fallback: LTP-based force close (used during WS reconnect) ───────────

    def force_close_all(self, kite):
        """
        Close all active trades using kite.ltp() as a fallback.
        Called when the WebSocket reconnects after a gap — avoids zombie trades.
        """
        with self._lock:
            if not self._trades:
                return

            tokens = list(self.active_tokens())
            try:
                ltp_data = kite.ltp(tokens)
                for token_key, val in ltp_data.items():
                    token = int(str(token_key))
                    self._ltp[token] = float(val.get("last_price", 0))
            except Exception as exc:
                logger.error(f"[Monitor] force_close_all LTP fetch failed: {exc}")

            now = datetime.now()
            for trade_id, trade in list(self._trades.items()):
                exit_premium = self._ltp.get(trade.option_token, 0.0)
                exit_spot    = self._ltp.get(trade.spot_token,   trade.entry_spot)
                self._record_exit(trade, "MARKET_CLOSE", exit_spot, exit_premium, now)

            self._trades.clear()
