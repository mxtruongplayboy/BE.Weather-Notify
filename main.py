import logging
import os
from contextlib import asynccontextmanager

import firebase_admin
from firebase_admin import credentials
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.services.scheduler import start_scheduler, stop_scheduler
from app.services.weather_client import close_client

cfg = get_settings()

logging.basicConfig(
    level=getattr(logging, cfg.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _init_firebase() -> None:
    if firebase_admin._apps:
        return  # already initialized
    creds_path = cfg.firebase_credentials_path
    if creds_path and os.path.isfile(creds_path):
        cred = credentials.Certificate(creds_path)
        firebase_admin.initialize_app(cred)
        logger.info("Firebase Admin SDK initialized from %s", creds_path)
    else:
        firebase_admin.initialize_app()
        logger.warning(
            "Firebase Admin SDK initialized without explicit credentials "
            "(FIREBASE_CREDENTIALS_PATH not set or missing). "
            "Using Application Default Credentials."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("BE.Weather-Notify starting up (Firestore mode) …")
    _init_firebase()
    start_scheduler()
    yield
    stop_scheduler()
    await close_client()
    logger.info("BE.Weather-Notify shut down.")


app = FastAPI(
    title="Weather Notify Service",
    version="2.0.0",
    description="FCM push notification service — reads subscriptions from Firestore.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.api.health import router as health_router          # noqa: E402
from app.api.v1.check_now import router as check_now_router  # noqa: E402

app.include_router(health_router)
app.include_router(check_now_router)
