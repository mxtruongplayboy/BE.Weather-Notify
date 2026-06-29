"""
Alert worker — runs every 15 minutes via APScheduler.
Reads device registrations and location subscriptions from Firestore.
For every active device + location:
  1. Fetch forecast + lightning + storms
  2. Check morning/evening summary windows
  3. Evaluate real-time alerts
  4. Dedup via Firestore alert_logs + send FCM
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import firebase_admin
from firebase_admin import firestore as fb_firestore

from app.config import get_settings
from app.services import ai_personalization, alert_engine, push_sender, weather_client
from app.services.alert_engine import Severity

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Firestore helpers
# ---------------------------------------------------------------------------

def _get_db():
    return fb_firestore.client()


def _is_sent(db, key: str, ttl_hours: int) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
    doc = db.collection("alert_logs").document(key).get()
    if not doc.exists:
        return False
    sent_at = doc.get("sentAt")
    if sent_at is None:
        return False
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=timezone.utc)
    return sent_at >= cutoff


def _mark_sent(
    db, key: str, alert_type: str, location_id: str, device_id: str,
    extra: dict | None = None,
) -> None:
    expire_at = datetime.now(timezone.utc) + timedelta(hours=25)
    doc: dict = {
        "sentAt": datetime.now(timezone.utc),
        "alertType": alert_type,
        "locationId": location_id,
        "deviceId": device_id,
        "expireAt": expire_at,
    }
    if extra:
        doc.update(extra)
    db.collection("alert_logs").document(key).set(doc)


def _cleanup_old_logs(db) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    old_docs = (
        db.collection("alert_logs")
        .where("sentAt", "<", cutoff)
        .stream()
    )
    count = 0
    for doc in old_docs:
        doc.reference.delete()
        count += 1
    if count:
        logger.info("alert_worker: cleaned %d old Firestore log entries", count)


# ---------------------------------------------------------------------------
# Scheduled window check
# ---------------------------------------------------------------------------

def _local_now(tz_str: str) -> datetime:
    try:
        return datetime.now(ZoneInfo(tz_str))
    except ZoneInfoNotFoundError:
        return datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))


def _inside_window(now_local: datetime, prefs: dict) -> bool:
    if not prefs.get("scheduledEnabled", True):
        return True
    now_mins = now_local.hour * 60 + now_local.minute
    from_mins = prefs.get("scheduledFromHour", 8) * 60 + prefs.get("scheduledFromMinute", 0)
    to_mins = prefs.get("scheduledToHour", 20) * 60 + prefs.get("scheduledToMinute", 0)
    if from_mins <= to_mins:
        return from_mins <= now_mins <= to_mins
    return now_mins >= from_mins or now_mins <= to_mins  # crosses midnight


# ---------------------------------------------------------------------------
# Core processing per location subscription
# ---------------------------------------------------------------------------

async def _process_location(
    db,
    device_id: str,
    fcm_token: str,
    timezone_str: str,
    location_id: str,
    location_data: dict,
    device_data: dict,
) -> None:
    cfg = get_settings()
    now_local = _local_now(timezone_str)
    today = now_local.strftime("%Y-%m-%d")
    now_mins = now_local.hour * 60 + now_local.minute

    prefs = location_data.get("alertPrefs", {})
    lat = location_data.get("lat", 0.0)
    lon = location_data.get("lon", 0.0)
    name = location_data.get("name", "")

    pref_thunderstorm = prefs.get("thunderstorm", True)
    pref_strong_wind = prefs.get("strongWind", True)
    pref_heavy_rain = prefs.get("heavyRain", True)
    pref_heatwave = prefs.get("heatwave", True)
    pref_cold_snap = prefs.get("coldSnap", False)
    pref_lightning = prefs.get("lightning", True)
    pref_storm = prefs.get("storm", True)

    # Fetch weather data
    forecast = await weather_client.get_forecast(lat, lon)
    lightning = await weather_client.get_lightning(
        lat, lon,
        radius_km=cfg.lightning_radius_km,
        window_minutes=cfg.lightning_window_minutes,
    )
    storms = await weather_client.get_storms_nearby(lat, lon, radius_km=cfg.storm_radius_km)

    # ── Morning summary ────────────────────────────────────────────────────
    if prefs.get("morningEnabled", True):
        target = prefs.get("morningHour", 7) * 60 + prefs.get("morningMinute", 0)
        key = f"{device_id}__morning__{location_id}__{today}"
        if target <= now_mins < target + 15 and not _is_sent(db, key, cfg.summary_dedup_ttl_h):
            result = alert_engine.build_summary(
                name, forecast, lightning, storms,
                pref_thunderstorm=pref_thunderstorm,
                pref_strong_wind=pref_strong_wind,
                pref_heavy_rain=pref_heavy_rain,
                pref_heatwave=pref_heatwave,
                pref_cold_snap=pref_cold_snap,
                pref_lightning=pref_lightning,
                pref_storm=pref_storm,
            )
            if result:
                title, body = result
                ok, msg_id = push_sender.send_push(
                    fcm_token, title, body,
                    data={"type": "morning_summary", "locationId": location_id},
                )
                if ok:
                    _mark_sent(db, key, "morning_summary", location_id, device_id)
                elif msg_id and "INVALID_TOKEN" in str(msg_id):
                    _deactivate_device(db, device_id)
                    return

    # ── Evening summary ────────────────────────────────────────────────────
    if prefs.get("eveningEnabled", True):
        target = prefs.get("eveningHour", 18) * 60 + prefs.get("eveningMinute", 0)
        key = f"{device_id}__evening__{location_id}__{today}"
        if target <= now_mins < target + 15 and not _is_sent(db, key, cfg.summary_dedup_ttl_h):
            result = alert_engine.build_summary(
                name, forecast, lightning, storms,
                pref_thunderstorm=pref_thunderstorm,
                pref_strong_wind=pref_strong_wind,
                pref_heavy_rain=pref_heavy_rain,
                pref_heatwave=pref_heatwave,
                pref_cold_snap=pref_cold_snap,
                pref_lightning=pref_lightning,
                pref_storm=pref_storm,
            )
            if result:
                title, body = result
                ok, msg_id = push_sender.send_push(
                    fcm_token, title, body,
                    data={"type": "evening_summary", "locationId": location_id},
                )
                if ok:
                    _mark_sent(db, key, "evening_summary", location_id, device_id)
                elif msg_id and "INVALID_TOKEN" in str(msg_id):
                    _deactivate_device(db, device_id)
                    return

    # ── Real-time alerts ───────────────────────────────────────────────────
    inside_window = _inside_window(now_local, prefs)
    hour = now_local.hour

    # WATCH  → 2 slots: sáng [00–11], chiều/tối [12–23]
    watch_slot = 0 if hour < 12 else 1

    # WARNING → 3 slots: sáng [00–07], trưa [08–15], tối [16–23]
    warning_slot = 0 if hour < 8 else (1 if hour < 16 else 2)

    alerts = alert_engine.evaluate(
        forecast, lightning, storms,
        pref_thunderstorm=pref_thunderstorm,
        pref_strong_wind=pref_strong_wind,
        pref_heavy_rain=pref_heavy_rain,
        pref_heatwave=pref_heatwave,
        pref_cold_snap=pref_cold_snap,
        pref_lightning=pref_lightning,
        pref_storm=pref_storm,
        min_severity=Severity.WATCH,
    )

    # Pre-compute weather dict once — shared by all alerts in this cycle
    weather_dict = {
        "rainProb": min(forecast.max_rain_prob / 100.0, 1.0),
        "windBeaufort": forecast.max_wind_beaufort,
        "hasThunder": forecast.has_thunderstorm,
        "feelsLike": forecast.max_feels_like,
        "leadTimeMinutes": 30,
        "rainMm": forecast.rain_mm_3h,
    }

    for alert in alerts:
        if alert.severity < Severity.WARNING and not inside_window:
            continue

        slot = warning_slot if alert.severity >= Severity.WARNING else watch_slot
        key = f"{device_id}__{alert.alert_type}__{location_id}__{today}__s{slot}"
        if _is_sent(db, key, cfg.realtime_dedup_ttl_h):
            continue

        # ── AI Personalization ──────────────────────────────────────────────
        final_title = f"{alert.title} · {name}"
        final_body = alert.body
        ai_meta: dict = {
            "aiEnabled": False,
            "aiProbability": None,
            "aiFeatures": None,
            "outcome": None,
        }

        ai_data = device_data.get("aiPersonalization") or {}
        if (
            ai_data.get("enabled") is True
            and ai_data.get("transport")
            and ai_data.get("occupation")
        ):
            try:
                features = ai_personalization.build_ai_features(
                    device_data, weather_dict, now_local,
                )
                prob = ai_personalization.ai_predict(
                    list(ai_data.get("weights") or []),
                    float(ai_data.get("bias") or -4.844448),
                    features,
                )
                ai_meta = {
                    "aiEnabled": True,
                    "aiProbability": round(prob, 4),
                    "aiFeatures": features,
                    "outcome": None,
                }
                ai_content = ai_personalization.build_ai_notification_content(
                    alert.alert_type,
                    alert.severity.name,
                    weather_dict,
                    ai_data,
                    name,
                )
                if ai_content:
                    final_title, final_body = ai_content
            except Exception:
                logger.exception(
                    "alert_worker: AI personalization failed device=%s alert=%s",
                    device_id, alert.alert_type,
                )
        # ── End AI Block ────────────────────────────────────────────────────

        fcm_data: dict = {
            "type": alert.alert_type,
            "locationId": location_id,
            "severity": alert.severity.name,
            "alertLogId": key,
            "instanceId": device_id,
        }
        if ai_meta["aiFeatures"] is not None:
            fcm_data["aiFeatures"] = json.dumps(ai_meta["aiFeatures"])

        ok, msg_id = push_sender.send_push(
            fcm_token, final_title, final_body,
            data=fcm_data,
        )
        if ok:
            _mark_sent(db, key, alert.alert_type, location_id, device_id, extra=ai_meta)
        elif msg_id and "INVALID_TOKEN" in str(msg_id):
            _deactivate_device(db, device_id)
            return


def _deactivate_device(db, device_id: str) -> None:
    logger.warning("alert_worker: deactivating stale token device=%s", device_id)
    db.collection("devices").document(device_id).update({"isActive": False})


# ---------------------------------------------------------------------------
# Main entry point called by APScheduler
# ---------------------------------------------------------------------------

async def run_alert_check() -> None:
    logger.info("alert_worker: starting check cycle")
    db = _get_db()

    try:
        _cleanup_old_logs(db)

        devices = db.collection("devices").stream()
        device_count = 0

        for device_doc in devices:
            device_data = device_doc.to_dict()
            if not device_data:
                continue
            if not device_data.get("isActive", True):
                continue

            device_id = device_doc.id
            fcm_token = device_data.get("fcmToken", "")
            tz_str = device_data.get("timezone", "Asia/Ho_Chi_Minh")

            if not fcm_token:
                continue

            device_count += 1
            locations = db.collection("devices").document(device_id).collection("locations").stream()

            for loc_doc in locations:
                loc_data = loc_doc.to_dict()
                if not loc_data:
                    continue
                if not loc_data.get("isEnabled", True):
                    continue

                try:
                    await _process_location(
                        db,
                        device_id=device_id,
                        fcm_token=fcm_token,
                        timezone_str=tz_str,
                        location_id=loc_doc.id,
                        location_data=loc_data,
                        device_data=device_data,
                    )
                except Exception as exc:
                    logger.exception(
                        "alert_worker: error device=%s location=%s: %s",
                        device_id, loc_doc.id, exc,
                    )

        logger.info("alert_worker: checked %d active devices", device_count)
    except Exception as exc:
        logger.exception("alert_worker: cycle failed: %s", exc)

    logger.info("alert_worker: check cycle complete")
