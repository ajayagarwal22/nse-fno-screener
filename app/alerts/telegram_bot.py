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


def _get_bot() -> Optional[Bot]:
    global _bot
    if not settings.telegram_bot_token:
        return None
    if _bot is None:
        _bot = Bot(token=settings.telegram_bot_token)
    return _bot


def _escape_md(text: str) -> str:
    """Escape special chars for Telegram MarkdownV2."""
    special = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in text)


def _format_signal(signal: Signal) -> str:
    opt = signal.option
    opt_line = ""
    if opt:
        opt_line = (
            f"*Trade:* Buy `{opt.strike:.0f} {opt.option_type.value}` "
            f"`{opt.expiry.strftime('%d %b %Y')}` "
            f"\\(DTE={opt.days_to_expiry}, Prem={opt.current_premium:.2f}\\)\n"
        )

    reasons = "\n".join(f"• {_escape_md(r)}" for r in signal.reasons)

    confidence_emoji = {"A+": "🟢", "A-": "🟡", "B": "🟠"}.get(signal.confidence.value, "⚪")

    return (
        f"*{'=' * 30}*\n"
        f"*{_escape_md(signal.symbol)} {signal.direction.value} MOMENTUM SETUP*\n"
        f"*{'=' * 30}*\n\n"
        f"*Bias:* {signal.direction.value} {signal.trade_type.value}\n"
        f"*Confidence:* {confidence_emoji} {signal.confidence.value} \\(Score: {signal.gate_score:.0f}/100\\)\n\n"
        f"*Reason:*\n{reasons}\n\n"
        f"{opt_line}"
        f"*Entry:* {_escape_md(signal.entry_zone)}\n"
        f"*SL:* {_escape_md(signal.stop_loss)}\n"
        f"*Targets:* {_escape_md(signal.target_1)} \\| {_escape_md(signal.target_2)}\n"
        f"*R:R:* {signal.rr_ratio}\n"
        f"*Position Size:* {_escape_md(signal.position_sizing)}\n\n"
        f"⏱ _{_escape_md(signal.time_sensitivity)}_"
    )


async def send_signals(signals: list[Signal]) -> None:
    bot = _get_bot()
    if not bot or not settings.telegram_chat_id:
        logger.warning("Telegram not configured — skipping alert delivery")
        return

    for signal in signals:
        try:
            text = _format_signal(signal)
            await bot.send_message(
                chat_id=settings.telegram_chat_id,
                text=text,
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            logger.info(f"Telegram alert sent for {signal.symbol} {signal.direction.value}")
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
