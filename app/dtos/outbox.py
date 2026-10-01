from dataclasses import dataclass


@dataclass(frozen=True, slots=True, kw_only=True)
class OutboxDispatchResult:
    selected: int
    sent: int
    failed: int
