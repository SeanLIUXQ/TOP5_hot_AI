from datetime import UTC, date, datetime, timedelta


def utc_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def current_week_start(today: date | None = None) -> date:
    current = today or utc_now().date()
    return current - timedelta(days=current.weekday())


def previous_week_start(week_start: date) -> date:
    return week_start - timedelta(days=7)


def parse_week_start(value: str) -> date:
    parsed = date.fromisoformat(value)
    if parsed.weekday() != 0:
        raise ValueError("week_start must be a Monday date")
    return parsed

