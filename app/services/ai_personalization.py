"""
AI personalization — logistic regression for notification relevance.
Pure functions, no I/O. Called from alert_worker after threshold evaluation.
"""
import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_OUTDOOR_OCCUPATIONS = frozenset([
    "farmer", "fisherman", "outdoor_worker", "delivery", "vendor", "driver",
])
_OUTDOOR_ACTIVITIES = frozenset([
    "commute", "outdoor_work", "sport", "vendor", "fishing",
])
_TRANSPORT_RISK: dict[str, float] = {
    "motorbike": 1.0,
    "bicycle": 0.9,
    "walk": 0.85,
    "car": 0.15,
}


# ---------------------------------------------------------------------------
# Core prediction
# ---------------------------------------------------------------------------

def ai_predict(weights: list[float], bias: float, features: list[float]) -> float:
    z = bias + sum(w * f for w, f in zip(weights, features))
    return 1.0 / (1.0 + math.exp(-z))


# ---------------------------------------------------------------------------
# Feature vector (must match Flutter AIFeatureVector.build())
# ---------------------------------------------------------------------------

def build_ai_features(
    device_data: dict,
    weather: dict,
    now_local,  # datetime in device-local timezone
) -> list[float]:
    ai = device_data.get("aiPersonalization") or {}
    hour = now_local.hour
    dow = now_local.isoweekday()   # 1=Mon … 7=Sun (matches Flutter)

    schedule: list[dict] = ai.get("schedule") or []

    def _time_to_min(time_str: str) -> int:
        h, m = time_str.split(":")
        return int(h) * 60 + int(m)

    def _is_in_schedule() -> bool:
        if not schedule:
            return dow <= 5 and ((7 <= hour <= 9) or (17 <= hour <= 19))
        return any(
            dow in (s.get("days") or [])
            and abs(_time_to_min(s["time"]) - hour * 60) < 90
            for s in schedule
            if s.get("time")
        )

    def _is_outdoor_now() -> bool:
        if not _is_in_schedule():
            return False
        if not schedule:
            return ai.get("occupation", "") in _OUTDOOR_OCCUPATIONS
        return any(
            dow in (s.get("days") or [])
            and abs(_time_to_min(s["time"]) - hour * 60) < 90
            and s.get("activity", "") in _OUTDOOR_ACTIVITIES
            for s in schedule
            if s.get("time")
        )

    transport_risk = _TRANSPORT_RISK.get(ai.get("transport") or "", 0.5)
    lead_min = min(float(weather.get("leadTimeMinutes") or 30) / 120.0, 1.0)
    rain_prob = min(float(weather.get("rainProb") or 0.0), 1.0)
    wind_norm = min(float(weather.get("windBeaufort") or 0) / 12.0, 1.0)
    has_thunder = 1.0 if weather.get("hasThunder") else 0.0
    feels_like = float(weather.get("feelsLike") or 25.0)
    feels_norm = max(-1.0, min((feels_like - 20.0) / 30.0, 1.0))

    return [
        rain_prob,                                       # f0  rain probability
        wind_norm,                                       # f1  wind / 12
        has_thunder,                                     # f2  has thunderstorm
        feels_norm,                                      # f3  feels-like normalised
        1.0 if _is_in_schedule() else 0.0,              # f4  in active schedule
        dow / 7.0,                                       # f5  day of week / 7
        1.0 if dow >= 6 else 0.0,                       # f6  is weekend
        float(ai.get("recentOpenRate7d") or 0.5),       # f7  open rate 7d
        float(ai.get("recentDismissRate7d") or 0.5),    # f8  dismiss rate 7d
        lead_min,                                        # f9  lead time / 120
        transport_risk,                                  # f10 transport risk
        1.0 if _is_outdoor_now() else 0.0,              # f11 is outdoor now
    ]


# ---------------------------------------------------------------------------
# Personalised content (returns None → caller uses default alert content)
# ---------------------------------------------------------------------------

def build_ai_notification_content(
    alert_type: str,
    severity: str,         # "WARNING" | "WATCH" | ...
    weather: dict,
    ai: dict,
    location_name: str,
) -> Optional[tuple[str, str]]:
    transport = ai.get("transport") or ""
    occupation = ai.get("occupation") or ""

    is_motorbike = transport == "motorbike"
    is_car = transport == "car"
    is_walker = transport in ("walk", "bicycle")
    is_outdoor_job = occupation in _OUTDOOR_OCCUPATIONS

    beaufort = int(weather.get("windBeaufort") or 0)
    rain_mm = float(weather.get("rainMm") or 0.0)
    feels_like = float(weather.get("feelsLike") or 25.0)

    if alert_type == "strong_wind":
        label = "severe" if severity == "WARNING" else "strong"
        if is_motorbike:
            return (
                f"💨 {label.capitalize()} wind · Be careful on your motorbike",
                f"Level {beaufort} · Easy to lose control — slow down or avoid main roads",
            )
        if is_car:
            return (
                f"💨 {label.capitalize()} wind · {location_name}",
                f"Level {beaufort} · Watch out for tall trucks and exposed bridges",
            )
        if is_walker:
            return (
                f"💨 {label.capitalize()} wind · Hard to move around",
                f"Level {beaufort} · Better limit time outside if you can",
            )
        if occupation == "farmer":
            return (
                "💨 Strong wind · Affects farm work",
                f"Level {beaufort} · Put away tools and secure your crops",
            )

    elif alert_type == "heavy_rain":
        label = "very heavy" if severity == "WARNING" else "heavy"
        rain_str = f"{rain_mm:.1f}"
        if is_motorbike:
            return (
                f"🌧️ {label.capitalize()} rain · Slippery roads ahead",
                f"{rain_str}mm/3h · Bring a raincoat and slow down",
            )
        if is_car:
            return (
                f"🌧️ {label.capitalize()} rain · {location_name}",
                f"{rain_str}mm in the next 3 hours · Reduced visibility while driving",
            )
        if is_walker:
            return (
                f"🌧️ {label.capitalize()} rain · Bring an umbrella or raincoat",
                f"{rain_str}mm/3h · Prepare before heading out",
            )
        if occupation == "farmer":
            return (
                "🌧️ Heavy rain · Affects the harvest",
                f"{rain_str}mm · Check your drainage system",
            )

    elif alert_type == "thunderstorm":
        if is_outdoor_job:
            return (
                "⛈️ Thunderstorm · Dangerous for outdoor work",
                "Find shelter now, stay away from tall trees and open areas",
            )
        return (
            "⛈️ Thunderstorm approaching",
            "Storms expected within 6 hours · Find shelter, stay away from tall trees",
        )

    elif alert_type == "heatwave":
        feels_str = f"{feels_like:.0f}"
        if is_outdoor_job:
            return (
                "🌡️ Dangerous heat · Limit outdoor work",
                f"Feels like {feels_str}°C · Drink water, rest somewhere cool",
            )
        if is_motorbike:
            return (
                "🌡️ Heatwave · Take care on the road",
                f"Feels like {feels_str}°C · Drink water before heading out",
            )

    elif alert_type == "cold_snap":
        feels_str = f"{feels_like:.0f}"
        if is_motorbike or is_walker:
            return (
                "🥶 Deep cold · Dress warmly before heading out",
                f"Feels like {feels_str}°C · Wear enough layers, watch for slippery roads",
            )

    # lightning_nearby and storm_nearby: no transport/occupation variants
    return None
