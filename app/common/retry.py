import random
from datetime import UTC, datetime, timedelta

from app.core.settings import settings


def backoff_delay(attempts: int) -> datetime:
    base = settings.OUTBOX_BASE_RETRY_DELAY_SECONDS
    exponential = base * (2 ** max(attempts - 1, 0))
    jitter = random.uniform(0, exponential)  # noqa: S311
    return datetime.now(UTC) + timedelta(seconds=jitter)


def attempts_exhausted(attempts: int) -> bool:
    return attempts >= settings.OUTBOX_MAX_ATTEMPTS
