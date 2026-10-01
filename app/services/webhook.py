import asyncio
import logging
from typing import Any
from uuid import UUID

import httpx

from app.broker.producer import publish_payment_to_dlq
from app.common.enums import DeliveryStatus
from app.core.settings import settings
from app.services.notification import NotificationSender

logger = logging.getLogger(__name__)


class PaymentWebhookSender(NotificationSender):
    def __init__(
        self,
        max_attempts: int = settings.WEBHOOK_MAX_ATTEMPTS,
        base_delay_seconds: float = settings.WEBHOOK_BASE_DELAY_SECONDS,
        timeout_seconds: float = settings.WEBHOOK_TIMEOUT_SECONDS,
    ) -> None:
        self._max_attempts = max_attempts
        self._base_delay = base_delay_seconds
        self._timeout = timeout_seconds

    async def send(
        self,
        payment_id: UUID,
        target_url: str | None,
        payload: dict[str, Any],
    ) -> DeliveryStatus:
        if not target_url:
            logger.info("Webhook skipped: no url for payment_id=%s", payment_id)
            return DeliveryStatus.SKIPPED

        try:
            await self._send_with_retry(target_url, payload)
            return DeliveryStatus.DELIVERED
        except Exception as exc:
            logger.exception("Webhook delivery failed for payment_id=%s", payment_id)
            dlq_payload = {
                "payment_id": str(payment_id),
                "reason": f"webhook_failed: {exc}",
                "payload": payload,
            }
            try:
                await publish_payment_to_dlq(
                    dlq_payload,
                    message_id=str(payment_id),
                )
                return DeliveryStatus.DLQ_PUBLISHED
            except Exception:
                logger.exception("DLQ publish failed for payment_id=%s", payment_id)
                return DeliveryStatus.DLQ_PUBLISH_FAILED

    async def _send_with_retry(self, url: str, payload: dict[str, Any]) -> None:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for attempt in range(1, self._max_attempts + 1):
                try:
                    response = await client.post(url, json=payload)
                    if 200 <= response.status_code < 300:
                        logger.info(
                            "Webhook delivered: url=%s attempt=%s status=%s",
                            url,
                            attempt,
                            response.status_code,
                        )
                        return
                    raise RuntimeError(f"HTTP {response.status_code}")
                except (httpx.HTTPError, RuntimeError) as exc:
                    if attempt >= self._max_attempts:
                        raise
                    delay = self._base_delay * (2 ** (attempt - 1))
                    logger.warning(
                        "Webhook attempt %s/%s failed (%s). Retrying in %.1fs",
                        attempt,
                        self._max_attempts,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)
