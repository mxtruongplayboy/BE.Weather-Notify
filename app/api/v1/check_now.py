"""
POST /v1/check-now — on-demand alert check for a single device.

Runs the exact same pipeline as alert_worker._process_location:
  weather_client → alert_engine → AI personalization → AI content
But instead of sending FCM, saves alerts to:
  devices/{instanceId}/in_app_notifications/{uuid}

Called by the Flutter app when the user taps "Refresh" in the inbox.
"""
import logging
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from firebase_admin import firestore as fb_firestore

from app.services import ai_personalization, alert_engine, weather_client
from app.services.alert_engine import Severity

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["check-now"])


# ---------------------------------------------------------------------------
# Request / Response
# ---------------------------------------------------------------------------

class CheckNowRequest(BaseModel):
    instanceId: str
    timezone: str = "Asia/Ho_Chi_Minh"


class AlertFound(BaseModel):
    locationId: str
    locationName: str
    alertType: str
    severity: str
    title: str
    body: str
    aiEnabled: bool
    aiProbability: float | None


class CheckNowResponse(BaseModel):
    locationsChecked: int
    alertsFound: int
    alerts: list[AlertFound]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/check-now", response_model=CheckNowResponse)
async def check_now(req: CheckNowRequest):
    db = fb_firestore.client()

    # Load device data
    device_ref = db.collection("devices").document(req.instanceId)
    device_doc = device_ref.get()
    if not device_doc.exists:
        raise HTTPException(status_code=404, detail="Device not found")

    device_data = device_doc.to_dict() or {}

    try:
        now_local = datetime.now(ZoneInfo(req.timezone))
    except ZoneInfoNotFoundError:
        now_local = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))

    # Load enabled locations
    locations = [
        (loc_doc.id, loc_doc.to_dict())
        for loc_doc in device_ref.collection("locations").stream()
        if loc_doc.to_dict() and loc_doc.to_dict().get("isEnabled", True)
    ]

    found_alerts: list[AlertFound] = []

    for location_id, loc_data in locations:
        lat = loc_data.get("lat", 0.0)
        lon = loc_data.get("lon", 0.0)
        name = loc_data.get("name", "")
        prefs = loc_data.get("alertPrefs", {})

        try:
            forecast = await weather_client.get_forecast(lat, lon)
        except Exception as exc:
            logger.warning("check_now: forecast failed loc=%s: %s", location_id, exc)
            continue

        alerts = alert_engine.evaluate(
            forecast,
            weather_client.LightningData(),
            weather_client.StormData(),
            pref_thunderstorm=prefs.get("thunderstorm", True),
            pref_strong_wind=prefs.get("strongWind", True),
            pref_heavy_rain=prefs.get("heavyRain", True),
            pref_heatwave=prefs.get("heatwave", True),
            pref_cold_snap=prefs.get("coldSnap", False),
            pref_lightning=False,
            pref_storm=False,
            min_severity=Severity.WATCH,
        )

        # AI block — same as alert_worker
        weather_dict = {
            "rainProb": min(forecast.max_rain_prob / 100.0, 1.0),
            "windBeaufort": forecast.max_wind_beaufort,
            "hasThunder": forecast.has_thunderstorm,
            "feelsLike": forecast.max_feels_like,
            "leadTimeMinutes": 30,
            "rainMm": forecast.rain_mm_3h,
        }
        ai_data = device_data.get("aiPersonalization") or {}
        ai_ready = (
            ai_data.get("enabled") is True
            and bool(ai_data.get("transport"))
            and bool(ai_data.get("occupation"))
        )

        for alert in alerts:
            final_title = f"{alert.title} · {name}"
            final_body = alert.body
            ai_enabled = False
            ai_prob: float | None = None

            if ai_ready:
                try:
                    features = ai_personalization.build_ai_features(
                        device_data, weather_dict, now_local,
                    )
                    ai_prob = round(ai_personalization.ai_predict(
                        list(ai_data.get("weights") or []),
                        float(ai_data.get("bias") or -4.844448),
                        features,
                    ), 4)
                    ai_content = ai_personalization.build_ai_notification_content(
                        alert.alert_type, alert.severity.name,
                        weather_dict, ai_data, name,
                    )
                    if ai_content:
                        final_title, final_body = ai_content
                    ai_enabled = True
                except Exception:
                    logger.exception("check_now: AI failed loc=%s", location_id)

            # Save to in_app_notifications (Firestore listener picks it up)
            notif_id = str(uuid.uuid4())
            db.collection("devices").document(req.instanceId) \
              .collection("in_app_notifications").document(notif_id).set({
                "id": notif_id,
                "timestamp": datetime.now(timezone.utc),
                "alertType": alert.alert_type,
                "locationId": location_id,
                "locationName": name,
                "title": final_title,
                "body": final_body,
                "severity": alert.severity.name.lower(),
                "aiEnabled": ai_enabled,
                "aiProbability": ai_prob,
                "aiFeatures": None,
                "isRead": False,
                "source": "manualCheck",
                "weatherSnapshot": {
                    "rainProb": min(forecast.max_rain_prob / 100.0, 1.0),
                    "windBeaufort": forecast.max_wind_beaufort,
                    "hasThunder": forecast.has_thunderstorm,
                    "feelsLike": forecast.max_feels_like,
                },
            })

            found_alerts.append(AlertFound(
                locationId=location_id,
                locationName=name,
                alertType=alert.alert_type,
                severity=alert.severity.name,
                title=final_title,
                body=final_body,
                aiEnabled=ai_enabled,
                aiProbability=ai_prob,
            ))

            logger.info(
                "check_now: instanceId=%s loc=%s alert=%s sev=%s ai=%s",
                req.instanceId, name, alert.alert_type, alert.severity.name, ai_enabled,
            )

    return CheckNowResponse(
        locationsChecked=len(locations),
        alertsFound=len(found_alerts),
        alerts=found_alerts,
    )
