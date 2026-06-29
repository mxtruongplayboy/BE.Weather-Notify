from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.v1.deps import get_instance_id
from app.database import get_db
from app.models.device import DeviceRegistration
from app.models.subscription import LocationSubscription

router = APIRouter(prefix="/v1/notify", tags=["notify"])


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    fcmToken: str
    platform: str = "android"       # android | ios
    locale: Optional[str] = "vi"
    timezone: Optional[str] = "UTC"
    appVersion: Optional[str] = None


class AlertPrefsPayload(BaseModel):
    thunderstorm: bool = True
    strongWind: bool = True
    heavyRain: bool = True
    heatwave: bool = True
    coldSnap: bool = True
    scheduledEnabled: bool = True
    scheduledFromHour: int = 8
    scheduledFromMinute: int = 0
    scheduledToHour: int = 20
    scheduledToMinute: int = 0
    morningEnabled: bool = True
    morningHour: int = 7
    morningMinute: int = 0
    eveningEnabled: bool = True
    eveningHour: int = 18
    eveningMinute: int = 0


class LocationPayload(BaseModel):
    locationId: str
    name: str
    lat: float
    lon: float
    isEnabled: bool = True
    alertPrefs: AlertPrefsPayload = AlertPrefsPayload()


class SubscriptionsRequest(BaseModel):
    locations: list[LocationPayload]


# ---------------------------------------------------------------------------
# POST /v1/notify/register — register or refresh FCM token
# ---------------------------------------------------------------------------

@router.post("/register", status_code=200)
def register_device(
    req: RegisterRequest,
    app_instance_id: str = Depends(get_instance_id),
    db: Session = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    device = db.query(DeviceRegistration).filter(
        DeviceRegistration.app_instance_id == app_instance_id
    ).first()

    if device:
        device.fcm_token = req.fcmToken
        device.platform = req.platform
        device.locale = req.locale
        device.timezone = req.timezone
        device.app_version = req.appVersion
        device.is_active = True
        device.updated_at = now
    else:
        device = DeviceRegistration(
            app_instance_id=app_instance_id,
            fcm_token=req.fcmToken,
            platform=req.platform,
            locale=req.locale,
            timezone=req.timezone,
            app_version=req.appVersion,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(device)

    db.commit()
    return {"status": "registered", "appInstanceId": app_instance_id}


# ---------------------------------------------------------------------------
# PUT /v1/notify/subscriptions — full replace of watched locations
# ---------------------------------------------------------------------------

@router.put("/subscriptions", status_code=200)
def update_subscriptions(
    req: SubscriptionsRequest,
    app_instance_id: str = Depends(get_instance_id),
    db: Session = Depends(get_db),
):
    device = db.query(DeviceRegistration).filter(
        DeviceRegistration.app_instance_id == app_instance_id
    ).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not registered. Call /register first.")

    # Delete existing subscriptions then re-insert (full replace)
    db.query(LocationSubscription).filter(
        LocationSubscription.device_id == device.id
    ).delete()

    for loc in req.locations:
        p = loc.alertPrefs
        sub = LocationSubscription(
            device_id=device.id,
            location_id=loc.locationId,
            name=loc.name,
            lat=loc.lat,
            lon=loc.lon,
            is_enabled=loc.isEnabled,
            pref_thunderstorm=p.thunderstorm,
            pref_strong_wind=p.strongWind,
            pref_heavy_rain=p.heavyRain,
            pref_heatwave=p.heatwave,
            pref_cold_snap=p.coldSnap,
            pref_scheduled_enabled=p.scheduledEnabled,
            pref_scheduled_from_hour=p.scheduledFromHour,
            pref_scheduled_from_minute=p.scheduledFromMinute,
            pref_scheduled_to_hour=p.scheduledToHour,
            pref_scheduled_to_minute=p.scheduledToMinute,
            pref_morning_enabled=p.morningEnabled,
            pref_morning_hour=p.morningHour,
            pref_morning_minute=p.morningMinute,
            pref_evening_enabled=p.eveningEnabled,
            pref_evening_hour=p.eveningHour,
            pref_evening_minute=p.eveningMinute,
        )
        db.add(sub)

    db.commit()
    return {"status": "updated", "count": len(req.locations)}


# ---------------------------------------------------------------------------
# DELETE /v1/notify/unregister — remove device + all its subscriptions
# ---------------------------------------------------------------------------

@router.delete("/unregister", status_code=200)
def unregister_device(
    app_instance_id: str = Depends(get_instance_id),
    db: Session = Depends(get_db),
):
    device = db.query(DeviceRegistration).filter(
        DeviceRegistration.app_instance_id == app_instance_id
    ).first()
    if device:
        db.query(LocationSubscription).filter(
            LocationSubscription.device_id == device.id
        ).delete()
        db.delete(device)
        db.commit()
    return {"status": "unregistered"}


# ---------------------------------------------------------------------------
# GET /v1/notify/subscriptions — read current subscriptions (debug)
# ---------------------------------------------------------------------------

@router.get("/subscriptions", status_code=200)
def get_subscriptions(
    app_instance_id: str = Depends(get_instance_id),
    db: Session = Depends(get_db),
):
    device = db.query(DeviceRegistration).filter(
        DeviceRegistration.app_instance_id == app_instance_id
    ).first()
    if not device:
        return {"locations": []}
    subs = db.query(LocationSubscription).filter(
        LocationSubscription.device_id == device.id
    ).all()
    return {
        "locations": [
            {
                "locationId": s.location_id,
                "name": s.name,
                "lat": s.lat,
                "lon": s.lon,
                "isEnabled": s.is_enabled,
            }
            for s in subs
        ]
    }
