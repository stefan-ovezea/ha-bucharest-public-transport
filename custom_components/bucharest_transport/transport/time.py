"""GTFS service times, including DST and times beyond midnight."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Bucharest")


def service_epoch(day: date) -> float:
    """GTFS times start at local noon minus 12 elapsed hours, per the specification."""
    return datetime(day.year, day.month, day.day, 12, tzinfo=TZ).timestamp() - 43200


def seconds(value: str) -> int | None:
    """Parse a published time without interpolating missing values."""
    if not value:
        return None
    h, m, s = map(int, value.split(":"))
    if h < 0 or not 0 <= m < 60 or not 0 <= s < 60:
        raise ValueError(f"Invalid GTFS time: {value}")
    return h * 3600 + m * 60 + s


def iso(value: float | None) -> str | None:
    return datetime.fromtimestamp(value, UTC).isoformat() if value is not None else None


def service_dates(now: float, horizon: int, max_seconds: int) -> list[date]:
    today = datetime.fromtimestamp(now, TZ).date()
    return [
        today + timedelta(days=i) for i in range(-(max_seconds // 86400 + 1), horizon // 1440 + 2)
    ]
