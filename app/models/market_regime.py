from datetime import datetime
from sqlalchemy import Column, DateTime, Float, String, JSON, PrimaryKeyConstraint
from app.models.database import Base


class RegimeSnapshotModel(Base):
    __tablename__ = "regime_snapshots"
    __table_args__ = (PrimaryKeyConstraint("id", "timestamp"),)

    id = Column(String, nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True, default=datetime.utcnow)
    regime_type = Column(String(50), nullable=False)
    nifty_bias = Column(String(20))
    banknifty_bias = Column(String(20))
    overall_bias = Column(String(20))
    vix_level = Column(Float)
    vix_signal = Column(String(30))
    breadth_score = Column(Float)
    call_buying_env = Column(String(5))   # "true" | "false"
    put_buying_env = Column(String(5))
    reason = Column(String(500))
    raw = Column(JSON)
