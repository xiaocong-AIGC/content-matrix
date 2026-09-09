from datetime import datetime, timezone
from typing import Any


def to_utc_iso(value: datetime) -> str:
    """Render a datetime as an explicit UTC ISO-8601 string ending in ``Z``.

    SQLite drops ``tzinfo`` on round-trip, so timestamps created with an
    aware ``utcnow()`` come back naive. Naive datetimes are assumed to be UTC
    (which is how the whole codebase stores them) and tagged accordingly so the
    web client does not reinterpret them as local time.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_datetimes(data: Any) -> Any:
    """Recursively convert every datetime in a JSON-like structure to UTC ISO."""
    if isinstance(data, datetime):
        return to_utc_iso(data)
    if isinstance(data, dict):
        return {key: normalize_datetimes(value) for key, value in data.items()}
    if isinstance(data, list):
        return [normalize_datetimes(item) for item in data]
    return data
