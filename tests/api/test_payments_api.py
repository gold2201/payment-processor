from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from app.api.v1 import payments as payments_api
from app.common.enums import Currency, PaymentStatus
from app.core.settings import settings
from app.db.session import get_session
from app.dtos.payment import PaymentDTO
from app.main import app
from fastapi.testclient import TestClient

AUTH = {settings.API_KEY_NAME: settings.API_KEY}
VALID_BODY = {
    "amount": "100.50",
    "currency": "USD",
    "description": "Order #42",
    "metadata": {"order_id": "42"},
    "webhook_url": "https://example.com/hooks/payments",
}


def _dto(**overrides) -> PaymentDTO:
    data = {
        "payment_id": uuid4(),
        "amount": Decimal("100.50"),
        "currency": Currency.USD,
        "description": "Order #42",
        "metadata": {"order_id": "42"},
        "status": PaymentStatus.PENDING,
        "webhook_url": "https://example.com/hooks/payments",
        "created_at": datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
        "processed_at": None,
    }
    data.update(overrides)
    return PaymentDTO(**data)


async def _fake_session():
    yield None


@pytest.fixture
def client():
    app.dependency_overrides[get_session] = _fake_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def service(monkeypatch):
    state = SimpleNamespace(dto=_dto(), calls=[])

    class FakeService:
        def __init__(self, _session):
            pass

        async def create_payment(self, data, idempotency_key):
            state.calls.append((data, idempotency_key))
            return state.dto

        async def get_payment(self, _payment_id):
            return state.dto

    monkeypatch.setattr(payments_api, "PaymentService", FakeService)
    return state


class TestAuth:
    def test_health_is_public(self, client):
        assert client.get("/health").status_code == 200

    def test_missing_api_key_is_401(self, client, service):
        response = client.post("/api/v1/payments", json=VALID_BODY, headers={"Idempotency-Key": "k"})
        assert response.status_code == 401

    def test_wrong_api_key_is_403(self, client, service):
        headers = {settings.API_KEY_NAME: "wrong", "Idempotency-Key": "k"}
        response = client.post("/api/v1/payments", json=VALID_BODY, headers=headers)
        assert response.status_code == 403

    def test_get_requires_api_key(self, client, service):
        assert client.get(f"/api/v1/payments/{uuid4()}").status_code == 401


class TestCreatePayment:
    def test_returns_202_with_payment_id_status_and_created_at(self, client, service):
        response = client.post("/api/v1/payments", json=VALID_BODY, headers={**AUTH, "Idempotency-Key": "abc"})

        assert response.status_code == 202
        body = response.json()
        assert body["payment_id"] == str(service.dto.payment_id)
        assert body["status"] == "pending"
        assert "created_at" in body

    def test_passes_idempotency_key_and_body_to_service(self, client, service):
        client.post("/api/v1/payments", json=VALID_BODY, headers={**AUTH, "Idempotency-Key": "abc"})

        data, key = service.calls[0]
        assert key == "abc"
        assert data.amount == Decimal("100.50")
        assert data.currency == Currency.USD
        assert data.metadata == {"order_id": "42"}

    def test_idempotency_key_is_required(self, client, service):
        response = client.post("/api/v1/payments", json=VALID_BODY, headers=AUTH)

        assert response.status_code == 422
        assert service.calls == []

    def test_empty_idempotency_key_is_rejected(self, client, service):
        response = client.post("/api/v1/payments", json=VALID_BODY, headers={**AUTH, "Idempotency-Key": ""})

        assert response.status_code == 422

    def test_optional_fields_can_be_omitted(self, client, service):
        body = {"amount": "10.00", "currency": "EUR"}

        response = client.post("/api/v1/payments", json=body, headers={**AUTH, "Idempotency-Key": "abc"})

        assert response.status_code == 202
        data, _ = service.calls[0]
        assert data.metadata == {}
        assert data.webhook_url is None

    @pytest.mark.parametrize(
        "patch",
        [
            {"currency": "GBP"},
            {"amount": "0"},
            {"amount": "-5"},
            {"amount": "10.123"},
            {"amount": "abc"},
            {"webhook_url": "not-a-url"},
            {"description": "x" * 256},
        ],
    )
    def test_invalid_body_is_rejected(self, client, service, patch):
        response = client.post(
            "/api/v1/payments",
            json={**VALID_BODY, **patch},
            headers={**AUTH, "Idempotency-Key": "abc"},
        )

        assert response.status_code == 422
        assert service.calls == []

    def test_missing_amount_is_rejected(self, client, service):
        body = {k: v for k, v in VALID_BODY.items() if k != "amount"}

        response = client.post("/api/v1/payments", json=body, headers={**AUTH, "Idempotency-Key": "abc"})

        assert response.status_code == 422


class TestGetPayment:
    def test_returns_payment_details(self, client, service):
        service.dto = _dto(status=PaymentStatus.SUCCEEDED, processed_at=datetime(2026, 10, 1, 12, 0, 4, tzinfo=UTC))

        response = client.get(f"/api/v1/payments/{service.dto.payment_id}", headers=AUTH)

        assert response.status_code == 200
        body = response.json()
        assert body["payment_id"] == str(service.dto.payment_id)
        assert Decimal(body["amount"]) == Decimal("100.50")
        assert body["currency"] == "USD"
        assert body["status"] == "succeeded"
        assert body["description"] == "Order #42"
        assert body["webhook_url"] == "https://example.com/hooks/payments"
        assert body["processed_at"] is not None

    def test_metadata_is_returned(self, client, service):
        service.dto = _dto(metadata={"order_id": "42", "source": "web"})

        response = client.get(f"/api/v1/payments/{service.dto.payment_id}", headers=AUTH)

        assert response.json()["metadata"] == {"order_id": "42", "source": "web"}

    def test_unknown_payment_is_404(self, client, service):
        service.dto = None

        response = client.get(f"/api/v1/payments/{uuid4()}", headers=AUTH)

        assert response.status_code == 404

    def test_invalid_uuid_is_422(self, client, service):
        assert client.get("/api/v1/payments/not-a-uuid", headers=AUTH).status_code == 422
