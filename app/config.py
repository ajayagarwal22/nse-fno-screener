from pydantic_settings import BaseSettings
from pydantic import Field


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
        env_file = ".env"
        extra = "ignore"


settings = Settings()
