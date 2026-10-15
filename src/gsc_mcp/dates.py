"""Date handling. Search Console reports every date in Pacific Time (America/Los_Angeles)."""

from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime
from zoneinfo import ZoneInfo

from gsc_mcp.errors import GuardError

PACIFIC = ZoneInfo("America/Los_Angeles")

# Search Console keeps 16 months of performance data and about 10 days of hourly data.
HISTORY_MONTHS = 16
HOURLY_DAYS = 10
# About the last 3 days (today and the 2 before) are still being processed, so the day
# before them is usually the latest final one.
FRESH_DAYS = 3


def today_pacific() -> date:
    return datetime.now(PACIFIC).date()


def parse_date(value: str, *, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise GuardError(f"{field} must be a date in YYYY-MM-DD format, got {value!r}.") from None


def months_before(day: date, months: int) -> date:
    """The same day-of-month `months` earlier, clamped to the end of shorter months."""
    year, month_zero = divmod(day.year * 12 + (day.month - 1) - months, 12)
    month = month_zero + 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def span_days(start: date, end: date) -> int:
    return (end - start).days + 1
