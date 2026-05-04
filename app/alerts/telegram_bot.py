"""Telegram alert delivery using python-telegram-bot."""
import logging
from typing import Optional

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

from app.config import settings
from app.engines.entry_trigger import Signal

logger = logging.getLogger(__name__)

_bot: Optional[Bot] = None
_CONF_ORDER = {"A+": 0, "A-": 1, "B": 2}
_CONF_EMOJI = {"A+": "🟢", "A-": "🟡", "B": "🟠"}


def _get_bot() -> Optional[Bot]:
    global _bot
    if not settings.telegram_bot_token:
        return None
    if _bot is None:
        _bot = Bot(token=settings.telegram_bot_token)
    return _bot


def _e(text: str) -> str:
    """Escape text for Telegram HTML parse mode."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_signal(signal: Signal) -> str:
    opt = signal.option
    emoji = _CONF_EMOJI.get(signal.confidence.value, "⚪")
    direction_arrow = "📈" if signal.direction.value == "CALL" else "📉"

    # Header
    lines = [
        f"{emoji} <b>{_e(signal.symbol)} {signal.direction.value}</b>  "
        f"<code>{signal.confidence.value} · {signal.gate_score:.0f}%</code>",
        "",
    ]

    # Strike + premium block
    if opt:
        lines += [
            f"<b>Strike:</b>  <code>{opt.strike:.0f} {opt.option_type.value}</code>",
            f"<b>Premium:</b> <code>₹{opt.current_premium:.2f}</code>  "
            f"<b>Expiry:</b> {opt.expiry.strftime('%d %b')} (DTE {opt.days_to_expiry})",
            f"<b>IV:</b> {opt.iv:.1f}%" if opt.iv else "",
            "",
        ]

    # Trade levels — the core of the alert
    lines += [
        f"{direction_arrow} <b>Entry:</b>   {_e(signal.entry_zone)}",
        f"🛑 <b>SL:</b>      {_e(signal.stop_loss)}",
        f"🎯 <b>Target 1:</b> {_e(signal.target_1)}",
        f"🎯 <b>Target 2:</b> {_e(signal.target_2)}",
        f"↔️ <b>R:R:</b>     {_e(signal.rr_ratio)}",
        "",
    ]

    # Primary signals (first 3 reasons — divergence is always first)
    key_reasons = signal.reasons[:3]
    if key_reasons:
        lines.append("<b>Signals:</b>")
        lines += [f"• {_e(r)}" for r in key_reasons]
        lines.append("")

    # Footer
    lines += [
        f"💰 {_e(signal.position_sizing)}",
        f"⏱ <i>{_e(signal.time_sensitivity)}</i>",
    ]

    return "\n".join(l for l in lines if l != "" or lines.index(l) == 0)


async def send_signals(signals: list[Signal]) -> None:
    bot = _get_bot()
    if not bot or not settings.telegram_chat_id:
        logger.warning("Telegram not configured — skipping alert delivery")
        return

    # Only send signals at or above the configured confidence threshold
    threshold = _CONF_ORDER.get(settings.min_confidence_to_alert, 1)
    to_send = [s for s in signals if _CONF_ORDER.get(s.confidence.value, 3) <= threshold]

    for signal in to_send:
        try:
            text = _format_signal(signal)
            await bot.send_message(
                chat_id=settings.telegram_chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
            )
            logger.info(f"Telegram alert sent: {signal.symbol} {signal.direction.value} {signal.confidence.value}")
        except TelegramError as e:
            logger.error(f"Telegram send failed for {signal.symbol}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error sending Telegram alert: {e}")


async def send_text(message: str) -> None:
    """Send a plain text message to the configured chat."""
    bot = _get_bot()
    if not bot or not settings.telegram_chat_id:
        return
    try:
        await bot.send_message(chat_id=settings.telegram_chat_id, text=message)
    except Exception as e:
        logger.error(f"Telegram send_text failed: {e}")
