"""
Alert engine — evaluates weather data against user preferences and
returns a list of alerts to send. Stateless; dedup is handled by alert_worker.
"""
from dataclasses import dataclass, field
from enum import IntEnum

from app.services.weather_client import ForecastData, LightningData, StormData


# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------

class Severity(IntEnum):
    NONE = 0
    ADVISORY = 1   # daily summaries only
    WATCH = 2      # real-time, within scheduled window — 2×/day
    WARNING = 3    # always, even outside quiet hours — 3×/day


# ---------------------------------------------------------------------------
# Alert
# ---------------------------------------------------------------------------

@dataclass
class Alert:
    alert_type: str
    severity: Severity
    title: str
    body: str
    emoji: str


# ---------------------------------------------------------------------------
# Thresholds — read from Firestore, defaults match Flutter systemThresholds
# ---------------------------------------------------------------------------

@dataclass
class Thresholds:
    # Wind (Beaufort)
    wind_advisory: float = 5.0
    wind_watch:    float = 6.0
    wind_warning:  float = 8.0
    # Rain (mm/3h)
    rain_advisory: float = 10.0
    rain_watch:    float = 25.0
    rain_warning:  float = 50.0
    # Heat (°C feels-like, above triggers)
    heat_advisory: float = 35.0
    heat_watch:    float = 38.0
    heat_warning:  float = 40.0
    # Cold (°C feels-like, below triggers)
    cold_advisory: float = 15.0
    cold_watch:    float = 10.0
    cold_warning:  float = 5.0

    @classmethod
    def from_device_data(cls, device_data: dict) -> "Thresholds":
        """
        Build thresholds from Firestore device document.

        AI ON  → effectiveThresholds (AI-adjusted watch) > systemThresholds > defaults
        AI OFF → systemThresholds > defaults  (effectiveThresholds ignored)
        """
        ai_enabled = (device_data.get("aiPersonalization") or {}).get("enabled", False)
        system     = device_data.get("systemThresholds") or {}
        effective  = (device_data.get("effectiveThresholds") or {}) if ai_enabled else {}

        def _eff(typ: str, level: str) -> float | None:
            v = (effective.get(typ) or {}).get(level)
            return float(v) if v is not None else None

        def _sys(typ: str, level: str) -> float | None:
            v = (system.get(typ) or {}).get(level)
            return float(v) if v is not None else None

        def _pick(typ: str, level: str, default: float) -> float:
            # effectiveThresholds only carries 'watch'; use for watch when AI is on
            if level == "watch" and ai_enabled:
                return _eff(typ, level) or _sys(typ, level) or default
            return _sys(typ, level) or default

        return cls(
            wind_advisory = _pick("wind", "advisory", 5.0),
            wind_watch    = _pick("wind", "watch",    6.0),
            wind_warning  = _pick("wind", "warning",  8.0),
            rain_advisory = _pick("rain", "advisory", 10.0),
            rain_watch    = _pick("rain", "watch",    25.0),
            rain_warning  = _pick("rain", "warning",  50.0),
            heat_advisory = _pick("heat", "advisory", 35.0),
            heat_watch    = _pick("heat", "watch",    38.0),
            heat_warning  = _pick("heat", "warning",  40.0),
            cold_advisory = _pick("cold", "advisory", 15.0),
            cold_watch    = _pick("cold", "watch",    10.0),
            cold_warning  = _pick("cold", "warning",  5.0),
        )


# ---------------------------------------------------------------------------
# Internal severity helpers — use Thresholds
# ---------------------------------------------------------------------------

def _wind_severity(beaufort: int, t: Thresholds) -> Severity:
    if beaufort >= t.wind_warning:  return Severity.WARNING
    if beaufort >= t.wind_watch:    return Severity.WATCH
    if beaufort >= t.wind_advisory: return Severity.ADVISORY
    return Severity.NONE


def _rain_severity(mm: float, prob: float, t: Thresholds) -> Severity:
    if mm >= t.rain_warning:  return Severity.WARNING
    if mm >= t.rain_watch:    return Severity.WATCH
    if mm >= t.rain_advisory: return Severity.ADVISORY
    # Probability-based fallback (only when mm is low)
    if prob >= 80: return Severity.WATCH
    if prob >= 60: return Severity.ADVISORY
    return Severity.NONE


def _heat_severity(feels_like: float, t: Thresholds) -> Severity:
    if feels_like >= t.heat_warning:  return Severity.WARNING
    if feels_like >= t.heat_watch:    return Severity.WATCH
    if feels_like >= t.heat_advisory: return Severity.ADVISORY
    return Severity.NONE


def _cold_severity(feels_like: float, t: Thresholds) -> Severity:
    if feels_like < t.cold_warning:  return Severity.WARNING
    if feels_like < t.cold_watch:    return Severity.WATCH
    if feels_like < t.cold_advisory: return Severity.ADVISORY
    return Severity.NONE


def _fmt_hour(h: int) -> str:
    """Convert local hour (0-23) to an English time expression."""
    if h < 0:    return "in the next few hours"
    if h == 0:   return "midnight"
    if h < 12:   return f"{h} AM"
    if h == 12:  return "noon"
    return f"{h - 12} PM"


# ---------------------------------------------------------------------------
# Evaluation — bulletin-style content with timing
# ---------------------------------------------------------------------------

def evaluate(
    forecast: ForecastData,
    lightning: LightningData,
    storms: StormData,
    *,
    pref_thunderstorm: bool,
    pref_strong_wind: bool,
    pref_heavy_rain: bool,
    pref_heatwave: bool,
    pref_cold_snap: bool,
    pref_lightning: bool = True,
    pref_storm: bool = True,
    min_severity: Severity = Severity.WATCH,
    thresholds: Thresholds | None = None,
) -> list[Alert]:
    t = thresholds or Thresholds()
    alerts: list[Alert] = []

    # ── Thunderstorm ──────────────────────────────────────────────────────
    if pref_thunderstorm and forecast.has_thunderstorm:
        when = _fmt_hour(forecast.first_thunder_hour)
        alerts.append(Alert(
            alert_type="thunderstorm",
            severity=Severity.WARNING,
            title=f"⛈️ Thunderstorm at {when}",
            body=f"Lightning and heavy rain are expected around {when}. Head home early and stay away from tall trees.",
            emoji="⛈️",
        ))

    # ── Strong wind ───────────────────────────────────────────────────────
    if pref_strong_wind:
        sev = _wind_severity(forecast.max_wind_beaufort, t)
        if sev >= min_severity:
            when = _fmt_hour(forecast.peak_wind_hour)
            intensity = {Severity.ADVISORY: "light", Severity.WATCH: "strong", Severity.WARNING: "severe"}[sev]
            advice = {
                Severity.ADVISORY: "Take care on a motorbike.",
                Severity.WATCH:    "Ride carefully, avoid open roads.",
                Severity.WARNING:  "Avoid going out; stay away from high, exposed areas.",
            }[sev]
            alerts.append(Alert(
                alert_type="strong_wind",
                severity=sev,
                title=f"💨 {intensity.capitalize()} wind from {when}",
                body=f"Level {forecast.max_wind_beaufort} ({int(forecast.max_wind_kph)} km/h) expected from {when}. {advice}",
                emoji="💨",
            ))

    # ── Heavy rain ────────────────────────────────────────────────────────
    if pref_heavy_rain:
        sev = _rain_severity(forecast.rain_mm_3h, forecast.max_rain_prob, t)
        if sev >= min_severity:
            when = _fmt_hour(forecast.peak_rain_hour)
            intensity = {Severity.ADVISORY: "light", Severity.WATCH: "moderate", Severity.WARNING: "heavy"}[sev]
            if forecast.rain_mm_3h >= t.rain_advisory:
                body = f"{forecast.rain_mm_3h:.1f} mm expected from {when}. Bring a raincoat."
            else:
                body = f"{int(forecast.max_rain_prob)}% chance of rain around {when}. Better bring a raincoat just in case."
            alerts.append(Alert(
                alert_type="heavy_rain",
                severity=sev,
                title=f"🌧️ {intensity.capitalize()} rain from {when}",
                body=body,
                emoji="🌧️",
            ))

    # ── Rain shower (from weather code, not yet captured by mm) ──────────
    if pref_heavy_rain and forecast.has_rain_shower:
        if _rain_severity(forecast.rain_mm_3h, forecast.max_rain_prob, t) < Severity.WATCH:
            when = _fmt_hour(forecast.peak_rain_hour)
            alerts.append(Alert(
                alert_type="heavy_rain",
                severity=Severity.WATCH,
                title=f"🌦️ Rain shower around {when}",
                body=f"A sudden shower may appear around {when}. Keep a raincoat handy.",
                emoji="🌦️",
            ))

    # ── Heatwave ──────────────────────────────────────────────────────────
    if pref_heatwave:
        sev = _heat_severity(forecast.max_feels_like, t)
        if sev >= min_severity:
            when = _fmt_hour(forecast.peak_heat_hour)
            intensity = {Severity.ADVISORY: "intense", Severity.WATCH: "extreme", Severity.WARNING: "dangerous"}[sev]
            advice = {
                Severity.ADVISORY: "Limit time outdoors during peak hours.",
                Severity.WATCH:    "Drink plenty of water, avoid going out from 10am–4pm.",
                Severity.WARNING:  "Dangerous for your health. Avoid going outside unless necessary.",
            }[sev]
            alerts.append(Alert(
                alert_type="heatwave",
                severity=sev,
                title=f"🌡️ {intensity.capitalize()} heat around {when}",
                body=f"Feels-like temperature reaching {int(forecast.max_feels_like)}°C around {when}. {advice}",
                emoji="🌡️",
            ))

    # ── Cold snap ─────────────────────────────────────────────────────────
    if pref_cold_snap:
        sev = _cold_severity(forecast.min_feels_like, t)
        if sev >= min_severity:
            when = _fmt_hour(forecast.peak_cold_hour)
            intensity = {Severity.ADVISORY: "light", Severity.WATCH: "deep", Severity.WARNING: "extreme"}[sev]
            advice = {
                Severity.ADVISORY: "Wear an extra jacket when heading out.",
                Severity.WATCH:    "Dress warmly and limit time outdoors.",
                Severity.WARNING:  "Dangerously cold. Wear layers and protect your hands, feet and face.",
            }[sev]
            alerts.append(Alert(
                alert_type="cold_snap",
                severity=sev,
                title=f"🥶 {intensity.capitalize()} cold around {when}",
                body=f"Feels-like temperature dropping to {int(forecast.min_feels_like)}°C around {when}. {advice}",
                emoji="🥶",
            ))

    # ── Lightning nearby ──────────────────────────────────────────────────
    if pref_lightning and lightning.count > 0:
        dist_str = f"{lightning.nearest_km} km" if lightning.nearest_km else "nearby"
        alerts.append(Alert(
            alert_type="lightning_nearby",
            severity=Severity.WARNING,
            title="⚡ Lightning activity nearby",
            body=f"{lightning.count} lightning strike(s) recorded within {dist_str}. Stay away from open areas, trees and power poles.",
            emoji="⚡",
        ))

    # ── Storm nearby ──────────────────────────────────────────────────────
    if pref_storm and storms.count > 0:
        dist_str = f"{storms.nearest_km} km" if storms.nearest_km else "nearby"
        name_str = f" — {storms.nearest_name}" if storms.nearest_name else ""
        alerts.append(Alert(
            alert_type="storm_nearby",
            severity=Severity.WARNING,
            title=f"🌀 Storm {dist_str} away{name_str}",
            body=f"{storms.count} active storm(s) in your area. Follow weather bulletins and prepare a response plan.",
            emoji="🌀",
        ))

    return alerts


def build_summary(
    location_name: str,
    forecast: ForecastData,
    lightning: LightningData,
    storms: StormData,
    *,
    day_label: str,
    pref_thunderstorm: bool,
    pref_strong_wind: bool,
    pref_heavy_rain: bool,
    pref_heatwave: bool,
    pref_cold_snap: bool,
    pref_lightning: bool = True,
    pref_storm: bool = True,
    thresholds: Thresholds | None = None,
) -> tuple[str, str]:
    """
    Build (title, body) for the morning/evening daily bulletin.

    Unlike `evaluate()`, this ALWAYS returns content — a daily bulletin is a
    general roundup ("what's the weather like"), not conditional on
    something noteworthy happening. Notable events (rain/wind/heat/etc. at
    ADVISORY level or above) are folded in as extra bullet points when they
    exist; a calm, unremarkable day still gets a plain summary sentence.

    `day_label`: e.g. "Today" (morning bulletin) or "Tomorrow" (evening
    bulletin) — becomes part of the title.
    """
    lo = round(forecast.min_feels_like)
    hi = round(forecast.max_feels_like)
    temp_part = f"Feels like {lo}–{hi}°C" if lo != hi else f"Feels like {hi}°C"

    if forecast.daily_rain_mm >= 1:
        rain_part = f"{forecast.daily_rain_mm:.1f}mm of rain expected"
    elif forecast.max_rain_prob >= 30:
        rain_part = f"{int(forecast.max_rain_prob)}% chance of rain"
    else:
        rain_part = "mostly dry"

    summary_bits = [temp_part, rain_part]
    if forecast.max_wind_beaufort >= 4:
        summary_bits.append(f"wind up to level {forecast.max_wind_beaufort}")

    alerts = evaluate(
        forecast, lightning, storms,
        pref_thunderstorm=pref_thunderstorm,
        pref_strong_wind=pref_strong_wind,
        pref_heavy_rain=pref_heavy_rain,
        pref_heatwave=pref_heatwave,
        pref_cold_snap=pref_cold_snap,
        pref_lightning=pref_lightning,
        pref_storm=pref_storm,
        min_severity=Severity.ADVISORY,
        thresholds=thresholds,
    )

    body = ", ".join(summary_bits) + "."
    if alerts:
        highlights = " • ".join(f"{a.emoji} {a.title.split(' ', 1)[-1]}" for a in alerts)
        body = f"{body} {highlights}"

    title = f"{day_label}'s weather · {location_name}"
    return title, body
