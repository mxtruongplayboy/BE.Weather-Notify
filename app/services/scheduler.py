import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_settings
from app.services.alert_worker import run_alert_check

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def start_scheduler() -> None:
    global _scheduler
    cfg = get_settings()

    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        run_alert_check,
        trigger="interval",
        seconds=cfg.alert_check_interval_s,
        id="alert_check",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "scheduler: alert_check registered every %ds",
        cfg.alert_check_interval_s,
    )


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("scheduler: stopped")
