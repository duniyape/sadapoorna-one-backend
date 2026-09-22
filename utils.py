from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))

def convert_utc_to_ist(value):
    """Recursively convert datetime values from UTC to IST in dicts, lists, and tuples."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(IST)
    if isinstance(value, dict):
        return {k: convert_utc_to_ist(v) for k, v in value.items()}
    if isinstance(value, list):
        return [convert_utc_to_ist(item) for item in value]
    if isinstance(value, tuple):
        return tuple(convert_utc_to_ist(item) for item in value)
    return value
