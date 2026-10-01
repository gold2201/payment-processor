from typing import Any

RETRY_COUNT_HEADER = "x-retry-count"


def get_retry_count(message: Any) -> int:
    headers = getattr(message, "headers", None) or {}
    raw = headers.get(RETRY_COUNT_HEADER, 0)
    try:
        return max(int(raw), 0)
    except (TypeError, ValueError):
        return 0
