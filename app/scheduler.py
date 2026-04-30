"""APScheduler setup for periodic market scans during NSE market hours."""
import logging
from datetime import datetime, time
from typing import Callable, Awaitable
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")
_MARKET_OPEN = time(9, 15)
_MARKET_CLOSE = time(15, 30)

scheduler = AsyncIOScheduler(timezone=_IST)

# Injected by main.py after the app boots so the scan job can broadcast via WebSocket
_on_signals_callback: Callable | None = None


def set_signals_callback(fn: Callable):
    """Register a callback invoked after each scan with the resulting signals list."""
    global _on_signals_callback
    _on_signals_callback = fn


def _is_market_hours() -> bool:
    now = datetime.now(_IST).time()
    return _MARKET_OPEN <= now <= _MARKET_CLOSE


async def _scan_job():
    if not _is_market_hours():
        return
    from app.screener import run_scan
    from app.alerts.telegram_bot import send_signals
    from app.alerts.file_exporter import export_signals
    from app.engines.entry_trigger import TradeType

    logger.info("Running scheduled scan...")
    signals = await run_scan(trade_type=TradeType.INTRADAY)
    if signals:
        await send_signals(signals)
        export_signals(signals)
        if _on_signals_callback:
            await _on_signals_callback(signals)
    logger.info(f"Scan complete. {len(signals)} signal(s) generated.")


async def _daily_bias_report():
    from app.engines.market_regime import analyze_market_regime
    from app.alerts.telegram_bot import send_text

    regime = analyze_market_regime()
    msg = (
        f"[Pre-Market Bias Report]\n"
        f"Regime: {regime.regime_type.value}\n"
        f"Nifty: {regime.nifty_bias.value} | BankNifty: {regime.banknifty_bias.value}\n"
        f"VIX: {regime.vix_data.value:.1f} ({regime.vix_data.signal})\n"
        f"Breadth: {regime.breadth.breadth_score:.0f}% advancing\n"
        f"Call env: {regime.call_buying_environment} | Put env: {regime.put_buying_environment}\n"
        f"Reason: {regime.reason}"
    )
    await send_text(msg)


async def _eod_export():
    from app.alerts.telegram_bot import send_text
    from app.alerts.file_exporter import get_today_export_path

    csv_path = get_today_export_path("csv")
    json_path = get_today_export_path("json")
    await send_text(
        f"[EOD Summary]\nCSV: {csv_path}\nJSON: {json_path}"
    )


def init_scheduler(scan_interval_minutes: int = 5):
    scheduler.add_job(_scan_job, "cron", minute=f"*/{scan_interval_minutes}",
                      hour="9-15", id="market_scan", replace_existing=True)
    scheduler.add_job(_daily_bias_report, "cron", hour=9, minute=0,
                      id="daily_bias", replace_existing=True)
    scheduler.add_job(_eod_export, "cron", hour=15, minute=35,
                      id="eod_export", replace_existing=True)
    scheduler.start()
    logger.info(f"Scheduler started. Scan every {scan_interval_minutes} min during market hours.")
