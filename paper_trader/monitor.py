"""
MonitorThread — single thread that checks ALL active trades on every tick.

Design:
  - One thread manages all trades (not one thread per trade).
  - KiteTicker callback delivers ticks → on_tick() updates LTP cache,
    checks WATCHING trades for entry, checks ACTIVE trades for exit.
  - Thread-safe: _trades dict and _ltp dict protected by RLock.
  - All DB writes go through db module (fire-and-forget or blocking).

Lifecycle:
  WATCHING → spot reaches entry zone → ACTIVE → SL/T1/T2/TIME/MARKET_CLOSE → CLOSED

Entry conditions (checked on every SPOT tick):
  CALL: spot_ltp >= entry_spot  (breakout above signal level)
  PUT:  spot_ltp <= entry_spot  (breakdown below signal level)

Exit conditions (checked on every SPOT tick once ACTIVE):
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
    entry_premium:  float       # 0.0 while WATCHING; filled on entry confirmation
    entry_spot:     float       # entry zone level (signal's spot at generation time)
    sl_spot:        float
    target1:        float
    target2:        float
    exit_time_rule: Optional[str] = None   # "14:30" or None
    status:         str = "WATCHING"       # "WATCHING" | "ACTIVE"


class MonitorThread:
    def __init__(self):
        self._trades: dict[int, ActiveTrade] = {}  # trade_id → ActiveTrade
        self._ltp:    dict[int, float]        = {}  # token    → last price
        self._lock    = threading.RLock()

    # ── Trade registry ────────────────────────────────────────────────────────

    def add_trade(self, trade: ActiveTrade):
        with self._lock:
            self._trades[trade.trade_id] = trade
        prem = f"{trade.entry_premium:.2f}" if trade.entry_premium else "pending"
        logger.info(
            f"[Monitor] +trade#{trade.trade_id} "
            f"{trade.symbol} {trade.direction} {trade.strike}{trade.option_type} "
            f"exp={trade.expiry} zone={trade.entry_spot:.2f} prem={prem}"
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

    def get_live_prices(self) -> dict[int, dict]:
        """Return {trade_id: {current_spot, current_premium, status}} for all monitored trades."""
        with self._lock:
            return {
                tid: {
                    "current_spot":    self._ltp.get(t.spot_token),
                    "current_premium": self._ltp.get(t.option_token),
                    "status":          t.status,
                }
                for tid, t in self._trades.items()
            }

    # ── Tick handler (called from KiteTicker thread) ──────────────────────────

    def on_tick(self, ticks: list):
        now    = datetime.now()
        now_hm = now.strftime("%H:%M")

        entries: list[tuple[ActiveTrade, float, float]] = []   # (trade, spot, opt_ltp)
        exits:   list[tuple[ActiveTrade, str, float, float]] = []  # (trade, reason, spot, opt_ltp)
        expires: list[int] = []   # trade_ids that timed out while WATCHING

        with self._lock:
            # 1. Update LTP cache
            for tick in ticks:
                token = tick.get("instrument_token")
                price = tick.get("last_price") or tick.get("ltp")
                if token and price:
                    self._ltp[token] = float(price)

            # 2. Evaluate every monitored trade
            for trade_id, trade in list(self._trades.items()):
                spot    = self._ltp.get(trade.spot_token)
                opt_ltp = self._ltp.get(trade.option_token, 0.0)

                if spot is None:
                    continue

                if trade.status == "WATCHING":
                    # Expire WATCHING trades at market close without entering
                    if now_hm >= EXIT_BEFORE_CLOSE:
                        expires.append(trade_id)
                        self._trades.pop(trade_id)
                    elif self._check_entry(trade, spot):
                        trade.status        = "ACTIVE"
                        trade.entry_premium = opt_ltp
                        entries.append((trade, round(spot, 2), round(opt_ltp, 2)))

                else:  # ACTIVE
                    reason = self._check_exit(trade, spot, now_hm)
                    if reason:
                        exits.append((trade, reason, round(spot, 2), round(opt_ltp, 2)))
                        self._trades.pop(trade_id)

        # 3. DB writes outside lock (blocking is fine here — separate thread)
        for trade_id in expires:
            db.set_trade_status(trade_id, "SKIPPED")
            logger.info(f"[Monitor] EXPIRED (never entered) trade#{trade_id}")

        for trade, spot, opt_ltp in entries:
            self._confirm_entry(trade, spot, opt_ltp, now)

        for trade, reason, spot, opt_ltp in exits:
            self._record_exit(trade, reason, spot, opt_ltp, now)

    # ── Entry check ───────────────────────────────────────────────────────────

    def _check_entry(self, trade: ActiveTrade, spot: float) -> bool:
        """Return True when spot has reached the entry zone."""
        if not trade.entry_spot:
            return True
        if trade.direction == "CALL":
            return spot >= trade.entry_spot   # breakout above signal level
        else:
            return spot <= trade.entry_spot   # breakdown below signal level

    def _confirm_entry(
        self,
        trade: ActiveTrade,
        entry_spot: float,
        entry_premium: float,
        now: datetime,
    ):
        db.confirm_entry(trade.trade_id, {
            "entry_premium": entry_premium,
            "entry_spot":    entry_spot,
            "entry_time":    now.isoformat(),
        })
        logger.info(
            f"[Monitor] ENTERED trade#{trade.trade_id} {trade.symbol} "
            f"{trade.strike}{trade.option_type} prem={entry_premium:.2f} spot={entry_spot:.2f}"
        )

    # ── Exit check ────────────────────────────────────────────────────────────

    def _check_exit(
        self,
        trade: ActiveTrade,
        spot: float,
        now_hm: str,
    ) -> Optional[str]:
        """Return exit reason string or None."""
        if trade.exit_time_rule and now_hm >= trade.exit_time_rule:
            return "TIME"
        if now_hm >= EXIT_BEFORE_CLOSE:
            return "MARKET_CLOSE"

        if trade.direction == "PUT":
            if trade.sl_spot  and spot >= trade.sl_spot:   return "SL"
            if trade.target2  and spot <= trade.target2:   return "T2"
            if trade.target1  and spot <= trade.target1:   return "T1"
        else:  # CALL
            if trade.sl_spot  and spot <= trade.sl_spot:   return "SL"
            if trade.target2  and spot >= trade.target2:   return "T2"
            if trade.target1  and spot >= trade.target1:   return "T1"
        return None

    def _record_exit(
        self,
        trade: ActiveTrade,
        reason: str,
        exit_spot: float,
        exit_premium: float,
        now: datetime,
    ):
        entry = trade.entry_premium or 0.0
        pnl        = exit_premium - entry
        pnl_pct    = (pnl / entry * 100) if entry else 0.0
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
        """Close all active trades using kite.ltp() during WS reconnect."""
        with self._lock:
            if not self._trades:
                return
            tokens = list(self.active_tokens())
            try:
                ltp_data = kite.ltp(tokens)
                for token_key, val in ltp_data.items():
                    self._ltp[int(str(token_key))] = float(val.get("last_price", 0))
            except Exception as exc:
                logger.error(f"[Monitor] force_close_all LTP fetch failed: {exc}")

            now = datetime.now()
            for trade_id, trade in list(self._trades.items()):
                if trade.status == "ACTIVE":
                    exit_premium = self._ltp.get(trade.option_token, 0.0)
                    exit_spot    = self._ltp.get(trade.spot_token, trade.entry_spot)
                    self._record_exit(trade, "MARKET_CLOSE", exit_spot, exit_premium, now)
                else:
                    db.set_trade_status(trade_id, "SKIPPED")
            self._trades.clear()
