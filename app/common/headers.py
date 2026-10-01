from typing import Any


def parse_retry_count(message: Any, queue_name: str) -> int:
    headers = getattr(message, "headers", None) or {}
    x_death = headers.get("x-death")
    if not isinstance(x_death, list):
        return 0

    max_count = 0
    for entry in x_death:
        if not isinstance(entry, dict):
            continue
        if entry.get("queue") != queue_name or entry.get("reason") != "rejected":
            continue
        count = entry.get("count", 0)
        if isinstance(count, int):
            max_count = max(max_count, count)
        elif isinstance(count, str) and count.isdigit():
            max_count = max(max_count, int(count))
    return max_count
