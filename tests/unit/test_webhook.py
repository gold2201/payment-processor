import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from app.common.enums import DeliveryStatus
from app.services import webhook as webhook_module
from app.services.webhook import PaymentWebhookSender

URL = "https://example.com/hook"
PAYLOAD = {"payment_id": "p-1", "status": "succeeded", "amount": "100.50", "currency": "USD"}


@pytest.fixture
def sleep(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(webhook_module, "asyncio", SimpleNamespace(sleep=mock))
    return mock


@pytest.fixture
def dlq(monkeypatch):
    mock = AsyncMock()
    monkeypatch.setattr(webhook_module, "publish_payment_to_dlq", mock)
    return mock


@pytest.fixture
def sender():
    return PaymentWebhookSender(max_attempts=3, base_delay_seconds=2, timeout_seconds=1)


async def test_skipped_when_no_url(sender, httpx_mock, sleep, dlq):
    status = await sender.send(uuid4(), None, PAYLOAD)

    assert status == DeliveryStatus.SKIPPED
    assert httpx_mock.get_requests() == []
    dlq.assert_not_awaited()


async def test_delivered_on_first_attempt(sender, httpx_mock, sleep, dlq):
    httpx_mock.add_response(url=URL, method="POST", status_code=200)

    status = await sender.send(uuid4(), URL, PAYLOAD)

    assert status == DeliveryStatus.DELIVERED
    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert json.loads(requests[0].content) == PAYLOAD
    sleep.assert_not_awaited()
    dlq.assert_not_awaited()


async def test_retries_with_exponential_delay_then_succeeds(sender, httpx_mock, sleep, dlq):
    httpx_mock.add_response(url=URL, method="POST", status_code=500)
    httpx_mock.add_response(url=URL, method="POST", status_code=503)
    httpx_mock.add_response(url=URL, method="POST", status_code=200)

    status = await sender.send(uuid4(), URL, PAYLOAD)

    assert status == DeliveryStatus.DELIVERED
    assert len(httpx_mock.get_requests()) == 3
    assert [call.args[0] for call in sleep.await_args_list] == [2, 4]
    dlq.assert_not_awaited()


async def test_network_error_is_retried(sender, httpx_mock, sleep, dlq):
    httpx_mock.add_exception(httpx.ConnectError("boom"), url=URL, method="POST")
    httpx_mock.add_response(url=URL, method="POST", status_code=200)

    status = await sender.send(uuid4(), URL, PAYLOAD)

    assert status == DeliveryStatus.DELIVERED
    assert len(httpx_mock.get_requests()) == 2


async def test_goes_to_dlq_after_all_attempts_failed(sender, httpx_mock, sleep, dlq):
    for _ in range(3):
        httpx_mock.add_response(url=URL, method="POST", status_code=500)
    payment_id = uuid4()

    status = await sender.send(payment_id, URL, PAYLOAD)

    assert status == DeliveryStatus.DLQ_PUBLISHED
    assert len(httpx_mock.get_requests()) == 3
    assert sleep.await_count == 2  # между 3 попытками две паузы
    dlq.assert_awaited_once()
    message = dlq.await_args.args[0]
    assert message["payment_id"] == str(payment_id)
    assert message["reason"].startswith("webhook_failed")
    assert message["payload"] == PAYLOAD


async def test_reports_failure_when_dlq_publish_fails(sender, httpx_mock, sleep, dlq):
    for _ in range(3):
        httpx_mock.add_response(url=URL, method="POST", status_code=500)
    dlq.side_effect = RuntimeError("rabbit is down")

    status = await sender.send(uuid4(), URL, PAYLOAD)

    assert status == DeliveryStatus.DLQ_PUBLISH_FAILED
