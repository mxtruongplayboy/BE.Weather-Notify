"""
Alert engine — evaluates weather data against user preferences and
returns a list of alerts to send. Stateless; dedup is handled by alert_worker.
"""
from dataclasses import dataclass
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
# Internal thresholds
# ---------------------------------------------------------------------------

def _wind_severity(beaufort: int) -> Severity:
    if beaufort >= 6: return Severity.WARNING   # ≥ 50 km/h
    if beaufort >= 4: return Severity.WATCH     # ≥ 29 km/h — noticeable on motorbike
    if beaufort >= 3: return Severity.ADVISORY  # ≥ 20 km/h
    return Severity.NONE


def _rain_severity(mm: float, prob: float = 0.0) -> Severity:
    # mm-based: 10mm/3h = heavy, 3mm = moderate
    if mm >= 10:    return Severity.WARNING
    if mm >= 3:     return Severity.WATCH
    if mm >= 1:     return Severity.ADVISORY
    # probability fallback when mm is low but likelihood is high
    if prob >= 80:  return Severity.WATCH
    if prob >= 60:  return Severity.ADVISORY
    return Severity.NONE


def _heat_severity(feels_like: float) -> Severity:
    if feels_like >= 38: return Severity.WARNING   # dangerous heat
    if feels_like >= 35: return Severity.WATCH     # very hot
    if feels_like >= 33: return Severity.ADVISORY
    return Severity.NONE


def _cold_severity(feels_like: float) -> Severity:
    if feels_like < 10: return Severity.WARNING    # severe cold (VN standard)
    if feels_like < 15: return Severity.WATCH      # cold for tropical climate
    if feels_like < 20: return Severity.ADVISORY
    return Severity.NONE


# ---------------------------------------------------------------------------
# Evaluation — content reflects live weather data at time of call
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
) -> list[Alert]:
    alerts: list[Alert] = []

    # ── Thunderstorm ──────────────────────────────────────────────────────
    if pref_thunderstorm and forecast.has_thunderstorm:
        alerts.append(Alert(
            alert_type="thunderstorm",
            severity=Severity.WARNING,
            title="⛈️ Dông bão sắp đến",
            body="Có dông trong 6 giờ tới. Tìm nơi trú ẩn, tránh xa cây cao.",
            emoji="⛈️",
        ))

    # ── Strong wind ───────────────────────────────────────────────────────
    if pref_strong_wind:
        sev = _wind_severity(forecast.max_wind_beaufort)
        if sev >= min_severity:
            labels = {Severity.ADVISORY: "nhẹ", Severity.WATCH: "mạnh", Severity.WARNING: "rất mạnh"}
            alerts.append(Alert(
                alert_type="strong_wind",
                severity=sev,
                title=f"💨 Gió {labels[sev]}",
                body=f"Cấp {forecast.max_wind_beaufort} · {int(forecast.max_wind_kph)} km/h",
                emoji="💨",
            ))

    # ── Heavy rain ────────────────────────────────────────────────────────
    if pref_heavy_rain:
        sev = _rain_severity(forecast.rain_mm_3h, forecast.max_rain_prob)
        if sev >= min_severity:
            labels = {Severity.ADVISORY: "nhẹ", Severity.WATCH: "vừa", Severity.WARNING: "lớn"}
            mm_str = f"{forecast.rain_mm_3h:.1f} mm" if forecast.rain_mm_3h >= 1 else f"{int(forecast.max_rain_prob)}% khả năng mưa"
            alerts.append(Alert(
                alert_type="heavy_rain",
                severity=sev,
                title=f"🌧️ Mưa {labels[sev]}",
                body=f"{mm_str} trong 3 giờ tới",
                emoji="🌧️",
            ))

    # ── Rain shower (from weather code) ──────────────────────────────────
    if pref_heavy_rain and forecast.has_rain_shower:
        # Only add if not already captured by mm check
        if _rain_severity(forecast.rain_mm_3h, forecast.max_rain_prob) < Severity.WATCH:
            alerts.append(Alert(
                alert_type="heavy_rain",
                severity=Severity.WATCH,
                title="🌦️ Mưa rào",
                body="Có mưa rào trong khu vực. Chuẩn bị áo mưa.",
                emoji="🌦️",
            ))

    # ── Heatwave ──────────────────────────────────────────────────────────
    if pref_heatwave:
        sev = _heat_severity(forecast.max_feels_like)
        if sev >= min_severity:
            labels = {Severity.ADVISORY: "gay gắt", Severity.WATCH: "cực đoan", Severity.WARNING: "nguy hiểm"}
            alerts.append(Alert(
                alert_type="heatwave",
                severity=sev,
                title=f"🌡️ Nắng nóng {labels[sev]}",
                body=f"Cảm giác như {int(forecast.max_feels_like)}°C. Hạn chế ra ngoài trưa.",
                emoji="🌡️",
            ))

    # ── Cold snap ─────────────────────────────────────────────────────────
    if pref_cold_snap:
        sev = _cold_severity(forecast.min_feels_like)
        if sev >= min_severity:
            labels = {Severity.ADVISORY: "nhẹ", Severity.WATCH: "đậm", Severity.WARNING: "cực đoan"}
            alerts.append(Alert(
                alert_type="cold_snap",
                severity=sev,
                title=f"🥶 Rét {labels[sev]}",
                body=f"Cảm giác như {int(forecast.min_feels_like)}°C. Giữ ấm cơ thể.",
                emoji="🥶",
            ))

    # ── Lightning nearby ──────────────────────────────────────────────────
    if pref_lightning and lightning.count > 0:
        dist_str = f"{lightning.nearest_km} km" if lightning.nearest_km else "gần đây"
        alerts.append(Alert(
            alert_type="lightning_nearby",
            severity=Severity.WARNING,
            title="⚡ Sét gần khu vực",
            body=f"{lightning.count} tia sét trong {dist_str}. Không ra ngoài trời trống.",
            emoji="⚡",
        ))

    # ── Storm nearby ──────────────────────────────────────────────────────
    if pref_storm and storms.count > 0:
        dist_str = f"{storms.nearest_km} km" if storms.nearest_km else "gần"
        name_str = f" · {storms.nearest_name}" if storms.nearest_name else ""
        alerts.append(Alert(
            alert_type="storm_nearby",
            severity=Severity.WARNING,
            title=f"🌀 Bão cách {dist_str}{name_str}",
            body=f"{storms.count} cơn bão đang hoạt động trong vùng theo dõi.",
            emoji="🌀",
        ))

    return alerts


def build_summary(
    location_name: str,
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
) -> tuple[str, str] | None:
    """Build (title, body) for daily morning/evening summary. Returns None if nothing notable."""
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
    )
    if not alerts:
        return None

    parts = [f"{a.emoji} {a.title.split(' ', 1)[-1]}" for a in alerts]
    title = f"Dự báo cảnh báo · {location_name}"
    body = " • ".join(parts)
    return title, body
