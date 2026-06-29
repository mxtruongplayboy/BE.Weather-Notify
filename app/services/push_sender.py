"""
Firebase Cloud Messaging sender.
Firebase Admin SDK is initialized in main.py lifespan before this is called.
Falls back to stub mode if Firebase is not initialized.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def is_stub() -> bool:
    try:
        import firebase_admin
        firebase_admin.get_app()
        return False
    except Exception:
        return True


def send_push(
    fcm_token: str,
    title: str,
    body: str,
    data: Optional[dict] = None,
) -> tuple[bool, Optional[str]]:
    """
    Send a single FCM notification.
    Returns (success, message_id_or_error).
    """
    if not fcm_token:
        logger.debug("push_sender: skipping — empty FCM token")
        return False, "EMPTY_TOKEN"

    if is_stub():
        logger.info(
            "push_sender [STUB] token=%.20s title=%r body=%.80r",
            fcm_token, title, body,
        )
        return True, "stub-id"

    try:
        from firebase_admin import messaging

        message = messaging.Message(
            token=fcm_token,
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            android=messaging.AndroidConfig(priority="high"),
            apns=messaging.APNSConfig(
                headers={"apns-priority": "10"},
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(sound="default", badge=1)
                ),
            ),
        )
        msg_id = messaging.send(message)
        logger.info("push_sender: sent msg_id=%s token=%.20s", msg_id, fcm_token)
        return True, msg_id

    except Exception as exc:
        err = str(exc)
        logger.warning("push_sender: failed token=%.20s error=%s", fcm_token, err)
        if any(x in err for x in ("UNREGISTERED", "NOT_FOUND", "INVALID_ARGUMENT")):
            return False, f"INVALID_TOKEN:{err}"
        return False, err
