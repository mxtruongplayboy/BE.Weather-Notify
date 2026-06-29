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
    if mm >= 10:    return Severity.WARNING
    if mm >= 3:     return Severity.WATCH
    if mm >= 1:     return Severity.ADVISORY
    if prob >= 80:  return Severity.WATCH
    if prob >= 60:  return Severity.ADVISORY
    return Severity.NONE


def _heat_severity(feels_like: float) -> Severity:
    if feels_like >= 38: return Severity.WARNING
    if feels_like >= 35: return Severity.WATCH
    if feels_like >= 33: return Severity.ADVISORY
    return Severity.NONE


def _cold_severity(feels_like: float) -> Severity:
    if feels_like < 10: return Severity.WARNING
    if feels_like < 15: return Severity.WATCH
    if feels_like < 20: return Severity.ADVISORY
    return Severity.NONE


def _fmt_hour(h: int) -> str:
    """Convert local hour (0-23) to Vietnamese time expression."""
    if h < 0:    return "trong vài giờ tới"
    if h == 0:   return "nửa đêm"
    if h < 5:    return f"{h} giờ đêm"
    if h < 12:   return f"{h} giờ sáng"
    if h == 12:  return "trưa"
    if h < 18:   return f"{h - 12} giờ chiều"
    if h < 22:   return f"{h} giờ tối"
    return f"{h} giờ đêm"


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
) -> list[Alert]:
    alerts: list[Alert] = []

    # ── Thunderstorm ──────────────────────────────────────────────────────
    if pref_thunderstorm and forecast.has_thunderstorm:
        when = _fmt_hour(forecast.first_thunder_hour)
        alerts.append(Alert(
            alert_type="thunderstorm",
            severity=Severity.WARNING,
            title=f"⛈️ Dông bão lúc {when}",
            body=f"Sét và mưa lớn dự báo xuất hiện lúc {when}. Nên về nhà trước và tránh xa cây cao.",
            emoji="⛈️",
        ))

    # ── Strong wind ───────────────────────────────────────────────────────
    if pref_strong_wind:
        sev = _wind_severity(forecast.max_wind_beaufort)
        if sev >= min_severity:
            when = _fmt_hour(forecast.peak_wind_hour)
            intensity = {Severity.ADVISORY: "nhẹ", Severity.WATCH: "mạnh", Severity.WARNING: "rất mạnh"}[sev]
            advice = {
                Severity.ADVISORY: "Chú ý khi đi xe máy.",
                Severity.WATCH:    "Cẩn thận khi đi xe máy, tránh đường trống trải.",
                Severity.WARNING:  "Hạn chế ra đường, tránh các khu vực cao và trống.",
            }[sev]
            alerts.append(Alert(
                alert_type="strong_wind",
                severity=sev,
                title=f"💨 Gió {intensity} từ {when}",
                body=f"Cấp {forecast.max_wind_beaufort} ({int(forecast.max_wind_kph)} km/h) dự kiến từ {when}. {advice}",
                emoji="💨",
            ))

    # ── Heavy rain ────────────────────────────────────────────────────────
    if pref_heavy_rain:
        sev = _rain_severity(forecast.rain_mm_3h, forecast.max_rain_prob)
        if sev >= min_severity:
            when = _fmt_hour(forecast.peak_rain_hour)
            intensity = {Severity.ADVISORY: "nhẹ", Severity.WATCH: "vừa", Severity.WARNING: "lớn"}[sev]
            if forecast.rain_mm_3h >= 1:
                body = f"{forecast.rain_mm_3h:.1f} mm dự kiến đổ xuống từ {when}. Mang theo áo mưa."
            else:
                body = f"Xác suất mưa {int(forecast.max_rain_prob)}% lúc {when}. Nên mang theo áo mưa đề phòng."
            alerts.append(Alert(
                alert_type="heavy_rain",
                severity=sev,
                title=f"🌧️ Mưa {intensity} từ {when}",
                body=body,
                emoji="🌧️",
            ))

    # ── Rain shower (from weather code, not yet captured by mm) ──────────
    if pref_heavy_rain and forecast.has_rain_shower:
        if _rain_severity(forecast.rain_mm_3h, forecast.max_rain_prob) < Severity.WATCH:
            when = _fmt_hour(forecast.peak_rain_hour)
            alerts.append(Alert(
                alert_type="heavy_rain",
                severity=Severity.WATCH,
                title=f"🌦️ Mưa rào lúc {when}",
                body=f"Mưa rào bất chợt có thể xuất hiện lúc {when}. Chuẩn bị áo mưa.",
                emoji="🌦️",
            ))

    # ── Heatwave ──────────────────────────────────────────────────────────
    if pref_heatwave:
        sev = _heat_severity(forecast.max_feels_like)
        if sev >= min_severity:
            when = _fmt_hour(forecast.peak_heat_hour)
            intensity = {Severity.ADVISORY: "gay gắt", Severity.WATCH: "cực đoan", Severity.WARNING: "nguy hiểm"}[sev]
            advice = {
                Severity.ADVISORY: "Hạn chế ra ngoài vào giờ đỉnh.",
                Severity.WATCH:    "Uống nhiều nước, hạn chế ra ngoài từ 10–16 giờ.",
                Severity.WARNING:  "Nguy hiểm cho sức khỏe. Không ra ngoài nếu không cần thiết.",
            }[sev]
            alerts.append(Alert(
                alert_type="heatwave",
                severity=sev,
                title=f"🌡️ Nắng nóng {intensity} lúc {when}",
                body=f"Nhiệt độ cảm giác chạm {int(forecast.max_feels_like)}°C lúc {when}. {advice}",
                emoji="🌡️",
            ))

    # ── Cold snap ─────────────────────────────────────────────────────────
    if pref_cold_snap:
        sev = _cold_severity(forecast.min_feels_like)
        if sev >= min_severity:
            when = _fmt_hour(forecast.peak_cold_hour)
            intensity = {Severity.ADVISORY: "nhẹ", Severity.WATCH: "đậm", Severity.WARNING: "cực đoan"}[sev]
            advice = {
                Severity.ADVISORY: "Mặc thêm áo khoác khi ra ngoài.",
                Severity.WATCH:    "Mặc đủ ấm, hạn chế ở ngoài trời lâu.",
                Severity.WARNING:  "Rét nguy hiểm. Mặc nhiều lớp, bảo vệ tay chân và mặt.",
            }[sev]
            alerts.append(Alert(
                alert_type="cold_snap",
                severity=sev,
                title=f"🥶 Rét {intensity} lúc {when}",
                body=f"Nhiệt độ cảm giác xuống {int(forecast.min_feels_like)}°C lúc {when}. {advice}",
                emoji="🥶",
            ))

    # ── Lightning nearby ──────────────────────────────────────────────────
    if pref_lightning and lightning.count > 0:
        dist_str = f"{lightning.nearest_km} km" if lightning.nearest_km else "gần đây"
        alerts.append(Alert(
            alert_type="lightning_nearby",
            severity=Severity.WARNING,
            title="⚡ Sét đang hoạt động gần đây",
            body=f"{lightning.count} tia sét ghi nhận trong vòng {dist_str}. Không đứng ngoài trời trống, tránh xa cây và cột điện.",
            emoji="⚡",
        ))

    # ── Storm nearby ──────────────────────────────────────────────────────
    if pref_storm and storms.count > 0:
        dist_str = f"{storms.nearest_km} km" if storms.nearest_km else "gần"
        name_str = f" — {storms.nearest_name}" if storms.nearest_name else ""
        alerts.append(Alert(
            alert_type="storm_nearby",
            severity=Severity.WARNING,
            title=f"🌀 Bão cách {dist_str}{name_str}",
            body=f"{storms.count} cơn bão đang hoạt động trong khu vực. Theo dõi bản tin thời tiết và chuẩn bị phương án ứng phó.",
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
