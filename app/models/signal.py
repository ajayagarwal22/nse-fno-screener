from datetime import datetime
from sqlalchemy import Column, DateTime, Float, Index, Integer, String, JSON
from app.models.database import Base


class SignalModel(Base):
    __tablename__ = "signals"

    id = Column(String, primary_key=True)
    timestamp = Column(DateTime, nullable=False, index=True, default=datetime.utcnow)
    symbol = Column(String(50), nullable=False, index=True)
    direction = Column(String(10), nullable=False, index=True)   # CALL | PUT
    trade_type = Column(String(20), nullable=False)               # INTRADAY | SWING
    confidence = Column(String(5), nullable=False, index=True)    # A+ | A- | B
    gate_score = Column(Float)

    # Option details
    strike = Column(Float)
    expiry = Column(String(20))
    option_type = Column(String(5))
    premium = Column(Float)
    iv = Column(Float)

    # Trade parameters
    entry_zone = Column(String(200))
    stop_loss = Column(String(200))
    target_1 = Column(String(200))
    target_2 = Column(String(200))
    rr_ratio = Column(String(20))
    position_sizing = Column(String(200))

    # Context
    regime_type = Column(String(50))
    vix_level = Column(Float)
    rsi_value = Column(Float)
    pcr_value = Column(Float)

    # Full payload for flexibility
    payload = Column(JSON)

    __table_args__ = (
        Index("ix_signals_ts_symbol", "timestamp", "symbol"),
    )
