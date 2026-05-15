import os
import time

from pydantic_settings import BaseSettings
from pydantic import Field


def _preload_env(env_path: str, retries: int = 5, delay: float = 1.0) -> None:
    """Read .env into os.environ with retries so iCloud sync hiccups don't crash startup."""
    for attempt in range(retries):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip()
                    if key and key not in os.environ:
                        os.environ[key] = val
            return
        except OSError:
            if attempt < retries - 1:
                time.sleep(delay)


_env_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
if os.path.exists(_env_file):
    _preload_env(_env_file)


class Settings(BaseSettings):
    kite_api_key: str = Field(default="", env="KITE_API_KEY")
    kite_api_secret: str = Field(default="", env="KITE_API_SECRET")
    kite_access_token: str = Field(default="", env="KITE_ACCESS_TOKEN")
    kite_request_token: str = Field(default="", env="KITE_REQUEST_TOKEN")

    telegram_bot_token: str = Field(default="", env="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", env="TELEGRAM_CHAT_ID")

    database_url: str = Field(
        default="postgresql+asyncpg://postgres:password@localhost:5432/nse_screener",
        env="DATABASE_URL",
    )

    scan_interval_minutes: int = Field(default=5, env="SCAN_INTERVAL_MINUTES")
    min_oi_threshold: int = Field(default=500, env="MIN_OI_THRESHOLD")
    min_volume_multiplier: float = Field(default=1.5, env="MIN_VOLUME_MULTIPLIER")
    max_bid_ask_spread_pct: float = Field(default=0.5, env="MAX_BID_ASK_SPREAD_PCT")
    min_confidence_to_alert: str = Field(default="A-", env="MIN_CONFIDENCE_TO_ALERT")

    high_risk_window_hours: int = Field(default=24, env="HIGH_RISK_WINDOW_HOURS")
    exports_dir: str = Field(default="./exports", env="EXPORTS_DIR")

    class Config:
        env_file = None  # Already loaded into os.environ above
        extra = "ignore"


settings = Settings()
