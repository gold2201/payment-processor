from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from app.broker import declarations
from app.common import retry as retry_module
from app.common.headers import RETRY_COUNT_HEADER, get_retry_count
from app.core.settings import settings


class TestGetRetryCount:
    def test_no_headers_means_first_delivery(self):
        assert get_retry_count(SimpleNamespace(headers=None)) == 0
        assert get_retry_count(SimpleNamespace(headers={})) == 0

    def test_object_without_headers_attribute(self):
        assert get_retry_count(object()) == 0

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(1, 1), (2, 2), ("2", 2), (0, 0), (-5, 0), ("abc", 0), (None, 0)],
    )
    def test_parses_header(self, raw, expected):
        msg = SimpleNamespace(headers={RETRY_COUNT_HEADER: raw})
        assert get_retry_count(msg) == expected


class TestOutboxBackoff:
    @pytest.mark.parametrize(("attempts", "expected_seconds"), [(1, 3), (2, 6), (3, 12)])
    def test_delay_grows_exponentially(self, monkeypatch, attempts, expected_seconds):
        monkeypatch.setattr(settings, "OUTBOX_BASE_RETRY_DELAY_SECONDS", 3)

        monkeypatch.setattr(retry_module, "random", SimpleNamespace(uniform=lambda _a, b: b))

        before = datetime.now(UTC)
        result = retry_module.backoff_delay(attempts)
        after = datetime.now(UTC)

        assert before + timedelta(seconds=expected_seconds) <= result <= after + timedelta(seconds=expected_seconds)

    def test_jitter_never_exceeds_exponential_cap(self, monkeypatch):
        monkeypatch.setattr(settings, "OUTBOX_BASE_RETRY_DELAY_SECONDS", 3)
        for _ in range(50):
            before = datetime.now(UTC)
            result = retry_module.backoff_delay(2)
            assert before <= result <= datetime.now(UTC) + timedelta(seconds=6)

    @pytest.mark.parametrize(("attempts", "exhausted"), [(1, False), (2, False), (3, True), (4, True)])
    def test_attempts_exhausted(self, monkeypatch, attempts, exhausted):
        monkeypatch.setattr(settings, "OUTBOX_MAX_ATTEMPTS", 3)
        assert retry_module.attempts_exhausted(attempts) is exhausted


class TestRetryTopology:
    @pytest.fixture(autouse=True)
    def _settings(self, monkeypatch):
        monkeypatch.setattr(settings, "MAX_CONSUMER_ATTEMPTS", 3)
        monkeypatch.setattr(settings, "PAYMENTS_RETRY_BASE_DELAY_MS", 5_000)
        monkeypatch.setattr(settings, "PAYMENTS_RETRY_QUEUE", "payments.new.retry")

    def test_three_attempts_mean_two_retry_levels(self):
        assert list(declarations.retry_levels()) == [1, 2]

    def test_single_attempt_has_no_retry_levels(self, monkeypatch):
        monkeypatch.setattr(settings, "MAX_CONSUMER_ATTEMPTS", 1)
        assert list(declarations.retry_levels()) == []

    def test_queue_names(self):
        assert declarations.retry_queue_name(1) == "payments.new.retry.1"
        assert declarations.retry_queue_name(2) == "payments.new.retry.2"

    def test_delay_doubles_on_each_level(self):
        assert [declarations.retry_delay_ms(level) for level in (1, 2, 3)] == [5_000, 10_000, 20_000]
