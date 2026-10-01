from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.common.enums import DeliveryStatus, ProcessingState
from app.core.settings import settings
from app.dtos.processing import PaymentProcessingResult
from app.workers import consumer


class _SessionCM:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *exc_info):
        return False


def _message(payment_id=None) -> dict:
    return {"payment_id": str(payment_id or uuid4())}


def _msg(retries: int = 0) -> SimpleNamespace:
    headers = {"x-retry-count": retries} if retries else {}
    return SimpleNamespace(headers=headers, ack=AsyncMock(), nack=AsyncMock())


def _result(payment_id, state=ProcessingState.PROCESSED) -> PaymentProcessingResult:
    return PaymentProcessingResult(
        payment_id=payment_id,
        state=state,
        webhook_url="https://example.com/hook",
        webhook_payload={"payment_id": str(payment_id), "status": "succeeded"},
    )


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(settings, "MAX_CONSUMER_ATTEMPTS", 3)

    state = SimpleNamespace(
        process=AsyncMock(),
        send_webhook=AsyncMock(return_value=DeliveryStatus.DELIVERED),
        retry=AsyncMock(),
        dlq=AsyncMock(),
    )

    class FakeProcessing:
        def __init__(self, _session):
            pass

        async def process_payment_created(self, payment_id):
            return await state.process(payment_id)

    monkeypatch.setattr(consumer, "async_session_factory", lambda: _SessionCM())
    monkeypatch.setattr(consumer, "PaymentProcessingService", FakeProcessing)
    monkeypatch.setattr(consumer, "webhook_sender", SimpleNamespace(send=state.send_webhook))
    monkeypatch.setattr(consumer, "publish_payment_retry", state.retry)
    monkeypatch.setattr(consumer, "publish_payment_to_dlq", state.dlq)
    return state


async def test_success_acks_and_sends_webhook(env):
    message = _message()
    payment_id = message["payment_id"]
    env.process.side_effect = lambda pid: _result(pid)
    msg = _msg()

    await consumer.handle_payment_created(message, msg)

    env.send_webhook.assert_awaited_once()
    assert env.send_webhook.await_args.kwargs["target_url"] == "https://example.com/hook"
    assert str(env.send_webhook.await_args.kwargs["payment_id"]) == payment_id
    msg.ack.assert_awaited_once()
    env.retry.assert_not_awaited()
    env.dlq.assert_not_awaited()


async def test_webhook_already_in_dlq_still_acks_original_message(env):
    env.process.side_effect = lambda pid: _result(pid)
    env.send_webhook.return_value = DeliveryStatus.DLQ_PUBLISHED
    msg = _msg()

    await consumer.handle_payment_created(_message(), msg)

    msg.ack.assert_awaited_once()
    env.retry.assert_not_awaited()


async def test_unknown_payment_is_acked_and_dropped(env):
    env.process.side_effect = lambda pid: PaymentProcessingResult(payment_id=pid, state=ProcessingState.NOT_FOUND)
    msg = _msg()

    await consumer.handle_payment_created(_message(), msg)

    msg.ack.assert_awaited_once()
    env.send_webhook.assert_not_awaited()
    env.retry.assert_not_awaited()


@pytest.mark.parametrize("bad_message", [{}, {"payment_id": None}, {"payment_id": "not-a-uuid"}])
async def test_invalid_payment_id_goes_straight_to_dlq(env, bad_message):
    msg = _msg()

    await consumer.handle_payment_created(bad_message, msg)

    env.dlq.assert_awaited_once()
    msg.ack.assert_awaited_once()
    env.process.assert_not_awaited()
    env.retry.assert_not_awaited()


@pytest.mark.parametrize(("previous_failures", "expected_level"), [(0, 1), (1, 2)])
async def test_processing_error_goes_to_next_retry_level(env, previous_failures, expected_level):
    message = _message()
    env.process.side_effect = RuntimeError("db is down")
    msg = _msg(retries=previous_failures)

    await consumer.handle_payment_created(message, msg)

    env.retry.assert_awaited_once()
    assert env.retry.await_args.args[0] == message
    assert env.retry.await_args.kwargs["failed_attempts"] == expected_level
    msg.ack.assert_awaited_once()
    env.dlq.assert_not_awaited()


async def test_goes_to_dlq_after_third_failed_attempt(env):
    message = _message()
    env.process.side_effect = RuntimeError("db is down")
    msg = _msg(retries=2)

    await consumer.handle_payment_created(message, msg)

    env.dlq.assert_awaited_once()
    dlq_message = env.dlq.await_args.args[0]
    assert dlq_message["reason"].startswith("consumer_retries_exhausted")
    env.retry.assert_not_awaited()
    msg.ack.assert_awaited_once()


async def test_webhook_and_dlq_both_failed_triggers_retry(env):
    env.process.side_effect = lambda pid: _result(pid)
    env.send_webhook.return_value = DeliveryStatus.DLQ_PUBLISH_FAILED
    msg = _msg()

    await consumer.handle_payment_created(_message(), msg)

    env.retry.assert_awaited_once()
    assert env.retry.await_args.kwargs["failed_attempts"] == 1
    msg.ack.assert_awaited_once()


async def test_retry_publish_failure_rejects_without_requeue(env):
    env.process.side_effect = RuntimeError("db is down")
    env.retry.side_effect = RuntimeError("rabbit is down")
    msg = _msg()

    await consumer.handle_payment_created(_message(), msg)

    msg.nack.assert_awaited_once_with(requeue=False)
    msg.ack.assert_not_awaited()
