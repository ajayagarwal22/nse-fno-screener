"""Export signals to dated JSON and CSV files in the exports directory."""
import csv
import json
import logging
from datetime import date, datetime
from dataclasses import asdict
from pathlib import Path
from typing import Union

from app.config import settings
from app.engines.entry_trigger import Signal

logger = logging.getLogger(__name__)


def get_today_export_path(fmt: str) -> Path:
    exports = Path(settings.exports_dir)
    exports.mkdir(parents=True, exist_ok=True)
    today = date.today().strftime("%Y-%m-%d")
    return exports / f"signals_{today}.{fmt}"


def _signal_to_dict(signal: Signal) -> dict:
    opt = signal.option
    return {
        "id": signal.id,
        "timestamp": signal.timestamp.isoformat(),
        "symbol": signal.symbol,
        "direction": signal.direction.value,
        "trade_type": signal.trade_type.value,
        "confidence": signal.confidence.value,
        "gate_score": signal.gate_score,
        "strike": opt.strike if opt else None,
        "expiry": opt.expiry.isoformat() if opt else None,
        "option_type": opt.option_type.value if opt else None,
        "premium": opt.current_premium if opt else None,
        "iv": opt.iv if opt else None,
        "entry_zone": signal.entry_zone,
        "stop_loss": signal.stop_loss,
        "target_1": signal.target_1,
        "target_2": signal.target_2,
        "rr_ratio": signal.rr_ratio,
        "position_sizing": signal.position_sizing,
        "regime_type": signal.regime_type,
        "vix_level": signal.vix_level,
        "rsi_value": signal.rsi_value,
        "pcr_value": signal.pcr_value,
        "reasons": signal.reasons,
        "oi_interpretation": signal.oi_interpretation,
    }


_CSV_FIELDS = [
    "id", "timestamp", "symbol", "direction", "trade_type", "confidence",
    "gate_score", "strike", "expiry", "option_type", "premium", "iv",
    "entry_zone", "stop_loss", "target_1", "target_2", "rr_ratio",
    "regime_type", "vix_level", "rsi_value", "pcr_value",
]


def export_signals(signals: list[Signal]) -> None:
    """Append signals to today's JSON and CSV export files."""
    if not signals:
        return

    rows = [_signal_to_dict(s) for s in signals]

    # --- JSON ---
    json_path = get_today_export_path("json")
    existing = []
    if json_path.exists():
        try:
            with open(json_path) as f:
                existing = json.load(f)
        except Exception:
            existing = []
    existing.extend(rows)
    with open(json_path, "w") as f:
        json.dump(existing, f, indent=2, default=str)
    logger.info(f"JSON export updated: {json_path}")

    # --- CSV ---
    csv_path = get_today_export_path("csv")
    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(rows)
    logger.info(f"CSV export updated: {csv_path}")
