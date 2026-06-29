from fastapi import APIRouter

import firebase_admin
from app.services.push_sender import is_stub

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    try:
        firebase_admin.get_app()
        firestore_ok = True
    except Exception:
        firestore_ok = False

    return {
        "status": "ok" if firestore_ok else "degraded",
        "firestore": "ok" if firestore_ok else "error",
        "fcm": "stub" if is_stub() else "live",
    }
