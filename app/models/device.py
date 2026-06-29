from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Index, String, Text, UniqueConstraint

from app.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class DeviceRegistration(Base):
    """One row per app instance. Holds the FCM push token."""

    __tablename__ = "device_registrations"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    app_instance_id = Column(String(128), nullable=False, unique=True, index=True)
    fcm_token = Column(Text, nullable=False)
    platform = Column(String(20), nullable=False, default="android")  # android | ios
    locale = Column(String(20), nullable=True, default="vi")
    timezone = Column(String(64), nullable=True, default="Asia/Ho_Chi_Minh")
    app_version = Column(String(32), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        Index("ix_dr_active", "is_active"),
    )
