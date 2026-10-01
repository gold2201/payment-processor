from enum import StrEnum


class Currency(StrEnum):
    RUB = "RUB"
    USD = "USD"
    EUR = "EUR"


class PaymentStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    PUBLISHED = "published"
    FAILED = "failed"


class ProcessingState(StrEnum):
    NOT_FOUND = "not_found"
    ALREADY_PROCESSED = "already_processed"
    PROCESSED = "processed"


class DeliveryStatus(StrEnum):
    DELIVERED = "delivered"
    SKIPPED = "skipped"
    DLQ_PUBLISHED = "dlq_published"
    DLQ_PUBLISH_FAILED = "dlq_publish_failed"


def enum_values(enum_cls: type[StrEnum]) -> list[str]:
    return [str(member) for member in enum_cls]
