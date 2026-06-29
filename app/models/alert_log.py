from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, Index, String, Text

from app.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class AlertLog(Base):
    """
    Deduplication log. One row per sent alert.
    dedup_key format: {app_instance_id}__{alert_type}__{location_id}__{YYYY-MM-DD}
    For real-time alerts the date is today; for summaries it also includes 'morning'/'evening'.
    """

    __tablename__ = "alert_logs"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    app_instance_id = Column(String(128), nullable=False, index=True)
    dedup_key = Column(Text, nullable=False, unique=True)
    alert_type = Column(String(64), nullable=False)    # thunderstorm | heavy_rain | …
    location_id = Column(String(64), nullable=False)
    fcm_message_id = Column(Text, nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)

    __table_args__ = (
        Index("ix_al_instance_sent", "app_instance_id", "sent_at"),
    )
