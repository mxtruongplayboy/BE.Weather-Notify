"""
HTTP client to fetch data from BE.Weather-Forecast and BE.Weather-Tracking.
Uses httpx with a shared async client for connection reuse.
"""
import logging
import math
from dataclasses import dataclass, field
from typing import Optional

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ForecastData:
    """Processed hourly data for the next 6 hours from current time."""
    has_thunderstorm: bool = False
    has_rain_shower: bool = False     # weather codes 80-82
    max_wind_kph: float = 0.0
    max_wind_beaufort: int = 0
    rain_mm_3h: float = 0.0          # precipitation sum, next 3 hours
    max_rain_prob: float = 0.0       # max precipitation_probability (0–100) next 3h
    max_feels_like: float = 0.0
    min_feels_like: float = 0.0
    daily_rain_mm: float = 0.0
    # Local hour (0-23) when each event peaks; -1 = unknown
    first_thunder_hour: int = -1
    peak_wind_hour: int = -1
    peak_rain_hour: int = -1
    peak_heat_hour: int = -1
    peak_cold_hour: int = -1


@dataclass
class LightningData:
    count: int = 0
    nearest_km: Optional[float] = None


@dataclass
class StormData:
    count: int = 0
    nearest_km: Optional[float] = None
    nearest_name: Optional[str] = None
    nearest_category: Optional[str] = None


# ---------------------------------------------------------------------------
# Beaufort conversion
# ---------------------------------------------------------------------------

def _beaufort(kph: float) -> int:
    if kph < 1: return 0
    if kph < 6: return 1
    if kph < 12: return 2
    if kph < 20: return 3
    if kph < 29: return 4
    if kph < 39: return 5
    if kph < 50: return 6
    if kph < 62: return 7
    if kph < 75: return 8
    if kph < 89: return 9
    if kph < 103: return 10
    if kph < 118: return 11
    return 12


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Shared HTTP client
# ---------------------------------------------------------------------------

_client: Optional[httpx.AsyncClient] = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        cfg = get_settings()
        _client = httpx.AsyncClient(timeout=cfg.http_timeout_s)
    return _client


async def close_client() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


# ---------------------------------------------------------------------------
# Forecast (Open-Meteo via BE.Weather-Forecast)
# ---------------------------------------------------------------------------

_THUNDERSTORM_CODES = {95, 96, 99}
_RAIN_SHOWER_CODES = {80, 81, 82}


async def get_forecast(lat: float, lon: float) -> ForecastData:
    cfg = get_settings()
    url = f"{cfg.forecast_be_url}/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": (
            "weather_code,wind_speed_10m,precipitation,"
            "precipitation_probability,apparent_temperature"
        ),
        "daily": "precipitation_sum",
        "forecast_days": 1,
        "timezone": "auto",
    }
    try:
        resp = await get_client().get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("weather_client: forecast fetch failed lat=%s lon=%s: %s", lat, lon, exc)
        return ForecastData()

    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        hourly = data["hourly"]
        times: list[str] = hourly["time"]

        # Use timezone from Open-Meteo response (matches the location's local time)
        tz_name = data.get("timezone", "UTC")
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            tz = ZoneInfo("UTC")
        now_local = datetime.now(tz)
        now_h = datetime(now_local.year, now_local.month, now_local.day, now_local.hour)
        start = 0
        for i, t in enumerate(times):
            if datetime.fromisoformat(t) >= now_h:
                start = i
                break

        def _next6(key: str) -> list:
            return hourly[key][start: start + 6]

        def _f(v, default=0.0): return float(v) if v is not None else default
        def _i(v, default=0):   return int(v)   if v is not None else default

        # Actual local hours for each of the 6 slots
        slot_hours = [
            datetime.fromisoformat(times[start + i]).hour
            if (start + i) < len(times) else -1
            for i in range(6)
        ]

        winds  = [_f(v) for v in _next6("wind_speed_10m")]
        precip = [_f(v) for v in _next6("precipitation")]
        codes  = [_i(v) for v in _next6("weather_code")]
        rain_prob_raw = hourly.get("precipitation_probability") or []
        rain_prob6 = [_f(v) for v in rain_prob_raw[start: start + 3]]

        # Temperature — keep (index, value) to find peak hours accurately
        feels_indexed = [
            (i, float(v))
            for i, v in enumerate(_next6("apparent_temperature"))
            if v is not None
        ]
        feels = [v for _, v in feels_indexed]

        max_wind = max(winds) if winds else 0.0
        rain_3h = sum(precip[:3])
        max_rain_prob = max(rain_prob6) if rain_prob6 else 0.0
        daily_rain = 0.0
        try:
            raw = ((data.get("daily") or {}).get("precipitation_sum") or [None])[0]
            daily_rain = _f(raw)
        except Exception:
            pass

        # ── Peak timing ───────────────────────────────────────────────────
        first_thunder_hour = next(
            (slot_hours[i] for i, c in enumerate(codes) if c in _THUNDERSTORM_CODES),
            -1,
        )

        peak_wind_hour = -1
        if winds:
            idx = winds.index(max(winds))
            peak_wind_hour = slot_hours[idx] if idx < len(slot_hours) else -1

        peak_rain_hour = -1
        rain3 = precip[:3]
        if rain3 and max(rain3) > 0:
            idx = rain3.index(max(rain3))
            peak_rain_hour = slot_hours[idx] if idx < len(slot_hours) else -1
        elif rain_prob6 and max(rain_prob6) > 0:
            idx = rain_prob6.index(max(rain_prob6))
            peak_rain_hour = slot_hours[idx] if idx < len(slot_hours) else -1

        peak_heat_hour = peak_cold_hour = -1
        if feels_indexed:
            hi_i, _ = max(feels_indexed, key=lambda x: x[1])
            lo_i, _ = min(feels_indexed, key=lambda x: x[1])
            peak_heat_hour = slot_hours[hi_i] if hi_i < len(slot_hours) else -1
            peak_cold_hour = slot_hours[lo_i] if lo_i < len(slot_hours) else -1

        return ForecastData(
            has_thunderstorm=any(c in _THUNDERSTORM_CODES for c in codes),
            has_rain_shower=any(c in _RAIN_SHOWER_CODES for c in codes),
            max_wind_kph=max_wind,
            max_wind_beaufort=_beaufort(max_wind),
            rain_mm_3h=rain_3h,
            max_rain_prob=max_rain_prob,
            max_feels_like=max(feels) if feels else 0.0,
            min_feels_like=min(feels) if feels else 0.0,
            daily_rain_mm=daily_rain,
            first_thunder_hour=first_thunder_hour,
            peak_wind_hour=peak_wind_hour,
            peak_rain_hour=peak_rain_hour,
            peak_heat_hour=peak_heat_hour,
            peak_cold_hour=peak_cold_hour,
        )
    except Exception as exc:
        logger.warning("weather_client: forecast parse error: %s", exc)
        return ForecastData()


# ---------------------------------------------------------------------------
# Full-day forecast — for morning (today) / evening (tomorrow) bulletins
# ---------------------------------------------------------------------------

async def get_forecast_for_day(lat: float, lon: float, day_offset: int) -> ForecastData:
    """
    Whole-day summary, unlike get_forecast() which only looks at the next 6
    hours from *now*. A 7am bulletin needs the full day ahead — a 3pm
    thunderstorm would be invisible to a 6-hour lookahead sent at 7am.

    day_offset: 0 = today (used by the morning bulletin), 1 = tomorrow
    (used by the evening bulletin).
    """
    cfg = get_settings()
    url = f"{cfg.forecast_be_url}/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": (
            "weather_code,wind_speed_10m,precipitation,"
            "precipitation_probability,apparent_temperature"
        ),
        "daily": "precipitation_sum",
        "forecast_days": max(2, day_offset + 1),
        "timezone": "auto",
    }
    try:
        resp = await get_client().get(url, params=params)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning(
            "weather_client: daily forecast fetch failed lat=%s lon=%s day_offset=%s: %s",
            lat, lon, day_offset, exc,
        )
        return ForecastData()

    try:
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        hourly = data["hourly"]
        times: list[str] = hourly["time"]

        tz_name = data.get("timezone", "UTC")
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            tz = ZoneInfo("UTC")
        now_local = datetime.now(tz)
        target_date = (now_local + timedelta(days=day_offset)).date()

        # Every hourly index whose local date matches the target day.
        idxs = [
            i for i, t in enumerate(times)
            if datetime.fromisoformat(t).date() == target_date
        ]
        if not idxs:
            return ForecastData()
        start, end = idxs[0], idxs[-1] + 1

        def _f(v, default=0.0): return float(v) if v is not None else default
        def _i(v, default=0):   return int(v)   if v is not None else default

        slot_hours = [datetime.fromisoformat(times[i]).hour for i in range(start, end)]

        winds  = [_f(v) for v in hourly["wind_speed_10m"][start:end]]
        precip = [_f(v) for v in hourly["precipitation"][start:end]]
        codes  = [_i(v) for v in hourly["weather_code"][start:end]]
        rain_prob_raw = (hourly.get("precipitation_probability") or [])[start:end]
        rain_probN = [_f(v) for v in rain_prob_raw]

        feels_indexed = [
            (i, float(v))
            for i, v in enumerate(hourly["apparent_temperature"][start:end])
            if v is not None
        ]
        feels = [v for _, v in feels_indexed]

        max_wind = max(winds) if winds else 0.0

        # Peak *rolling* 3-hour rain sum across the whole day — not the day
        # total. rain_mm_3h feeds thresholds calibrated for a 3h window
        # (rain_watch=25mm/3h etc.); a day total would blow past those for
        # perfectly ordinary drizzle spread over 24h.
        rain_mm_3h = 0.0
        peak_rain_hour = -1
        if len(precip) >= 3:
            window_sums = [sum(precip[i:i + 3]) for i in range(len(precip) - 2)]
            best = max(range(len(window_sums)), key=lambda i: window_sums[i])
            rain_mm_3h = window_sums[best]
            if rain_mm_3h > 0:
                peak_rain_hour = slot_hours[best]
        elif precip:
            rain_mm_3h = sum(precip)
            if rain_mm_3h > 0:
                peak_rain_hour = slot_hours[0]

        max_rain_prob = max(rain_probN) if rain_probN else 0.0
        if peak_rain_hour == -1 and rain_probN and max(rain_probN) > 0:
            idx = rain_probN.index(max(rain_probN))
            peak_rain_hour = slot_hours[idx]

        daily_rain = 0.0
        try:
            sums = (data.get("daily") or {}).get("precipitation_sum") or []
            raw = sums[day_offset] if day_offset < len(sums) else None
            daily_rain = _f(raw)
        except Exception:
            pass

        first_thunder_hour = next(
            (slot_hours[i] for i, c in enumerate(codes) if c in _THUNDERSTORM_CODES),
            -1,
        )

        peak_wind_hour = -1
        if winds:
            idx = winds.index(max(winds))
            peak_wind_hour = slot_hours[idx]

        peak_heat_hour = peak_cold_hour = -1
        if feels_indexed:
            hi_i, _ = max(feels_indexed, key=lambda x: x[1])
            lo_i, _ = min(feels_indexed, key=lambda x: x[1])
            peak_heat_hour = slot_hours[hi_i]
            peak_cold_hour = slot_hours[lo_i]

        return ForecastData(
            has_thunderstorm=any(c in _THUNDERSTORM_CODES for c in codes),
            has_rain_shower=any(c in _RAIN_SHOWER_CODES for c in codes),
            max_wind_kph=max_wind,
            max_wind_beaufort=_beaufort(max_wind),
            rain_mm_3h=rain_mm_3h,
            max_rain_prob=max_rain_prob,
            max_feels_like=max(feels) if feels else 0.0,
            min_feels_like=min(feels) if feels else 0.0,
            daily_rain_mm=daily_rain,
            first_thunder_hour=first_thunder_hour,
            peak_wind_hour=peak_wind_hour,
            peak_rain_hour=peak_rain_hour,
            peak_heat_hour=peak_heat_hour,
            peak_cold_hour=peak_cold_hour,
        )
    except Exception as exc:
        logger.warning("weather_client: daily forecast parse error: %s", exc)
        return ForecastData()


# ---------------------------------------------------------------------------
# Lightning (BE.Weather-Tracking)
# ---------------------------------------------------------------------------

async def get_lightning(lat: float, lon: float, radius_km: float, window_minutes: int) -> LightningData:
    cfg = get_settings()
    url = f"{cfg.tracking_be_url}/lightning/events/recent"
    params = {"sinceMinutes": window_minutes}
    try:
        resp = await get_client().get(url, params=params)
        resp.raise_for_status()
        events: list[dict] = resp.json().get("events", resp.json() if isinstance(resp.json(), list) else [])
    except Exception as exc:
        logger.warning("weather_client: lightning fetch failed: %s", exc)
        return LightningData()

    nearby = []
    for ev in events:
        try:
            elat = float(ev.get("latitude") or ev.get("lat", 0))
            elon = float(ev.get("longitude") or ev.get("lon", 0))
            dist = _haversine_km(lat, lon, elat, elon)
            if dist <= radius_km:
                nearby.append(dist)
        except Exception:
            continue

    if not nearby:
        return LightningData()
    return LightningData(count=len(nearby), nearest_km=round(min(nearby), 1))


# ---------------------------------------------------------------------------
# Storms (BE.Weather-Tracking)
# ---------------------------------------------------------------------------

async def get_storms_nearby(lat: float, lon: float, radius_km: float) -> StormData:
    cfg = get_settings()
    url = f"{cfg.tracking_be_url}/storms/active"
    try:
        resp = await get_client().get(url)
        resp.raise_for_status()
        storms = resp.json().get("storms", resp.json() if isinstance(resp.json(), list) else [])
    except Exception as exc:
        logger.warning("weather_client: storms fetch failed: %s", exc)
        return StormData()

    nearby = []
    for s in storms:
        try:
            slat = float(s.get("latitude") or s.get("lat") or s.get("current_lat", 0))
            slon = float(s.get("longitude") or s.get("lon") or s.get("current_lon", 0))
            dist = _haversine_km(lat, lon, slat, slon)
            if dist <= radius_km:
                nearby.append((dist, s.get("name", "Unknown"), s.get("category", "")))
        except Exception:
            continue

    if not nearby:
        return StormData()
    nearby.sort(key=lambda x: x[0])
    nearest = nearby[0]
    return StormData(
        count=len(nearby),
        nearest_km=round(nearest[0], 1),
        nearest_name=nearest[1],
        nearest_category=nearest[2],
    )
