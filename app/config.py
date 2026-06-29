from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Firebase ──────────────────────────────────────────────────────────────
    firebase_credentials_path: str = ""

    # ── Internal BE URLs ──────────────────────────────────────────────────────
    forecast_be_url: str = "http://omfc-api:8080/v1"
    tracking_be_url: str = "http://weather_tracking:8002/api/v1"

    # ── Scheduler ─────────────────────────────────────────────────────────────
    alert_check_interval_s: int = 900          # 15 minutes
    http_timeout_s: float = 10.0

    # ── Dedup TTL ─────────────────────────────────────────────────────────────
    realtime_dedup_ttl_h: int = 3
    summary_dedup_ttl_h: int = 23

    # ── Lightning / Storm thresholds ─────────────────────────────────────────
    lightning_radius_km: float = 50.0
    lightning_window_minutes: int = 30
    storm_radius_km: float = 500.0

    # ── Log ───────────────────────────────────────────────────────────────────
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
