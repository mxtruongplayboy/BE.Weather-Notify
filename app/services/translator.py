"""
Best-effort translation for outgoing notification text.

English is the system default (see alert_engine.py / ai_personalization.py —
every template is authored in English). When a device's `languageCode`
(synced from the Flutter app's current locale) is set to something else, we
try to translate the final title/body into that language right before
sending.

Translation is explicitly NOT allowed to be a hard dependency: if the
`deep-translator` package is missing, the network call fails, or the target
language isn't supported, we log a warning and fall back to the original
English text. A broken translator must never block or crash a send.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Flutter app locale codes -> Google Translate target codes. Only entries
# that don't map 1:1 need to be listed here.
_LANG_MAP: dict[str, str] = {
    "nb": "no",  # Norwegian Bokmål
    "zh": "zh-CN",  # Simplified Chinese (Flutter's default 'zh')
    "zh_TW": "zh-TW",  # Traditional Chinese
}


def _resolve_target(language_code: Optional[str]) -> Optional[str]:
    """None means "no translation needed" — either unset or already English."""
    if not language_code:
        return None
    code = language_code.strip()
    if not code or code.lower() in ("en", "en_us", "en-us"):
        return None
    return _LANG_MAP.get(code, code)


def translate_alert(
    title: str,
    body: str,
    language_code: Optional[str],
) -> tuple[str, str]:
    """
    Translate (title, body) into `language_code` when it's set and isn't
    English. Returns the original English strings unchanged whenever
    translation isn't needed, isn't available, or fails for any reason.
    """
    target = _resolve_target(language_code)
    if target is None:
        return title, body

    try:
        from deep_translator import GoogleTranslator

        translated_title = GoogleTranslator(source="en", target=target).translate(title)
        translated_body = GoogleTranslator(source="en", target=target).translate(body)
        if not translated_title or not translated_body:
            raise ValueError("empty translation result")
        return translated_title, translated_body
    except Exception as exc:
        logger.warning(
            "translator: failed target=%s title=%r error=%s — falling back to English",
            target, title, exc,
        )
        return title, body
