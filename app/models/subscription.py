from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Float,
    ForeignKey, Index, Integer, String,
)

from app.database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class LocationSubscription(Base):
    """
    One row per watched location per device.
    alert_prefs_* columns mirror Flutter AlertTypePrefs fields.
    """

    __tablename__ = "location_subscriptions"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    device_id = Column(BigInteger, ForeignKey("device_registrations.id", ondelete="CASCADE"), nullable=False, index=True)

    # Location identity (sent from Flutter WatchedLocation)
    location_id = Column(String(64), nullable=False)   # Flutter UUID
    name = Column(String(255), nullable=False)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    is_enabled = Column(Boolean, nullable=False, default=True)

    # ── Weather type toggles ────────────────────────────────────────────────
    pref_thunderstorm = Column(Boolean, nullable=False, default=True)
    pref_strong_wind = Column(Boolean, nullable=False, default=True)
    pref_heavy_rain = Column(Boolean, nullable=False, default=True)
    pref_heatwave = Column(Boolean, nullable=False, default=True)
    pref_cold_snap = Column(Boolean, nullable=False, default=False)

    # ── Scheduled quiet-hours window ────────────────────────────────────────
    pref_scheduled_enabled = Column(Boolean, nullable=False, default=True)
    pref_scheduled_from_hour = Column(Integer, nullable=False, default=8)
    pref_scheduled_from_minute = Column(Integer, nullable=False, default=0)
    pref_scheduled_to_hour = Column(Integer, nullable=False, default=20)
    pref_scheduled_to_minute = Column(Integer, nullable=False, default=0)

    # ── Morning summary ─────────────────────────────────────────────────────
    pref_morning_enabled = Column(Boolean, nullable=False, default=True)
    pref_morning_hour = Column(Integer, nullable=False, default=7)
    pref_morning_minute = Column(Integer, nullable=False, default=0)

    # ── Evening summary ─────────────────────────────────────────────────────
    pref_evening_enabled = Column(Boolean, nullable=False, default=True)
    pref_evening_hour = Column(Integer, nullable=False, default=18)
    pref_evening_minute = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        Index("ix_ls_device_location", "device_id", "location_id", unique=True),
        Index("ix_ls_enabled", "is_enabled"),
    )
