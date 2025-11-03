from datetime import datetime, timezone


def utc_now_iso() -> str:
    """Return current UTC time as ISO string with seconds precision."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
