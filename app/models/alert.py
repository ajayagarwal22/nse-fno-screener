from datetime import datetime
from sqlalchemy import Column, DateTime, String, Boolean, Text
from app.models.database import Base


class AlertLogModel(Base):
    __tablename__ = "alert_log"

    id = Column(String, primary_key=True)
    signal_id = Column(String, nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    channel = Column(String(30))          # TELEGRAM | FILE_JSON | FILE_CSV
    delivered = Column(Boolean, default=False)
    error = Column(Text, nullable=True)
