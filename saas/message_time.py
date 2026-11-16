"""Timestamp supplied by WhatsApp, distinct from when our database received a webhook."""

from datetime import datetime, timezone


def whatsapp_timestamp(value):
    try:
        if value in (None, ""):
            return None
        result = datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return result if 2020 <= result.year <= 2100 else None
