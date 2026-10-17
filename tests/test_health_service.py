"""Tests for HealthService: registered readiness checks, run concurrently."""

import logging
import threading
import time
from collections.abc import Callable, Generator
from unittest.mock import MagicMock

import pytest
from flask import Flask

from app.config import Settings
from app.services import health_service as health_module
from app.services.health_service import (
    READINESS_CHECK_DURATION_SECONDS,
    CheckResult,
    CheckStatus,
    HealthService,
)

OK = CheckResult(CheckStatus.OK)


@pytest.fixture(autouse=True)
def _app_context() -> Generator[None]:
    """Readyz needs an app context, and runs each check in one."""
    with Flask(__name__).app_context():
        yield


@pytest.fixture
def coordinator() -> MagicMock:
    coordinator = MagicMock()
    coordinator.is_shutting_down.return_value = False
    return coordinator


@pytest.fixture
def service(coordinator: MagicMock, test_settings: Settings) -> HealthService:
    return HealthService(lifecycle_coordinator=coordinator, settings=test_settings)


def _counting(result: CheckResult = OK) -> tuple[Callable[[], CheckResult], list[int]]:
    """A check returning ``result``, and the list its calls are counted in."""
    calls: list[int] = []

    def check() -> CheckResult:
        calls.append(1)
        return result

    return check, calls


class TestRegistration:
    def test_no_checks_is_ready(self, service: HealthService):
        body, status = service.check_readyz()

        assert status == 200
        assert body == {"status": "ok", "ready": True, "stage": "local-tst", "checks": {}}

    def test_reports_every_check_in_registration_order(self, service: HealthService):
        service.register_check("b", lambda: OK, critical=True)
        service.register_check("a", lambda: CheckResult(CheckStatus.OK, "fine"), critical=False)

        body, _ = service.check_readyz()

        assert list(body["checks"]) == ["b", "a"]
        assert body["checks"]["b"] == {"status": "ok", "critical": True}
        assert body["checks"]["a"] == {"status": "ok", "critical": False, "detail": "fine"}

    def test_register_replaces_a_check_of_the_same_name(self, service: HealthService):
        service.register_check("x", lambda: OK, critical=True)
        service.check_readyz()  # caches the first check's result

        service.register_check(
            "x", lambda: CheckResult(CheckStatus.ERROR, "second"), critical=False
        )
        body, status = service.check_readyz()

        assert status == 200
        assert body["checks"] == {"x": {"status": "error", "critical": False, "detail": "second"}}


class TestVerdict:
    def test_critical_failure_is_503(self, service: HealthService):
        service.register_check("db", lambda: CheckResult(CheckStatus.ERROR, "down"), critical=True)
        service.register_check("other", lambda: OK, critical=False)

        body, status = service.check_readyz()

        assert status == 503
        assert body["status"] == "not_ready"
        assert body["ready"] is False
        assert body["checks"]["db"] == {"status": "error", "critical": True, "detail": "down"}
        assert body["checks"]["other"] == {"status": "ok", "critical": False}

    def test_non_critical_failure_is_reported_and_stays_ready(self, service: HealthService):
        service.register_check("db", lambda: OK, critical=True)
        service.register_check(
            "gateway", lambda: CheckResult(CheckStatus.ERROR, "down"), critical=False
        )

        body, status = service.check_readyz()

        assert status == 200
        assert body["status"] == "degraded"
        assert body["ready"] is True
        assert body["checks"]["gateway"]["status"] == "error"

    def test_disabled_never_fails(self, service: HealthService):
        service.register_check(
            "mail", lambda: CheckResult(CheckStatus.DISABLED, "not configured"), critical=True
        )

        body, status = service.check_readyz()

        assert status == 200
        assert body["status"] == "ok"
        assert body["checks"]["mail"] == {
            "status": "disabled", "critical": True, "detail": "not configured",
        }

    def test_exception_is_the_checks_error(self, service: HealthService):
        def broken() -> CheckResult:
            raise RuntimeError("boom")

        service.register_check("broken", broken, critical=True)

        body, status = service.check_readyz()

        assert status == 503
        assert body["checks"]["broken"] == {"status": "error", "critical": True, "detail": "boom"}

    def test_exception_without_a_message_names_its_type(self, service: HealthService):
        def broken() -> CheckResult:
            raise TimeoutError

        service.register_check("broken", broken, critical=False)

        body, _ = service.check_readyz()

        assert body["checks"]["broken"]["detail"] == "TimeoutError"

    def test_one_failing_check_does_not_stop_the_others(self, service: HealthService):
        def broken() -> CheckResult:
            raise RuntimeError("boom")

        healthy, calls = _counting()
        service.register_check("broken", broken, critical=False)
        service.register_check("healthy", healthy, critical=True)

        body, status = service.check_readyz()

        assert status == 200
        assert body["checks"]["healthy"]["status"] == "ok"
        assert calls == [1]

    def test_shutting_down_runs_no_check(self, service: HealthService, coordinator: MagicMock):
        coordinator.is_shutting_down.return_value = True
        check, calls = _counting()
        service.register_check("db", check, critical=True)

        body, status = service.check_readyz()

        assert status == 503
        assert body == {"status": "shutting_down", "ready": False, "stage": "local-tst", "checks": {}}
        assert calls == []

    def test_checks_run_in_an_app_context(self, service: HealthService):
        from flask import has_app_context

        service.register_check(
            "ctx",
            lambda: CheckResult(CheckStatus.OK if has_app_context() else CheckStatus.ERROR),
            critical=True,
        )

        assert service.check_readyz()[1] == 200


class TestConcurrencyAndTimeouts:
    def test_checks_run_concurrently(self, service: HealthService):
        def slow() -> CheckResult:
            time.sleep(0.3)
            return OK

        for name in ("a", "b", "c"):
            service.register_check(name, slow, critical=True, timeout=2.0)

        started = time.monotonic()
        _, status = service.check_readyz()

        assert status == 200
        assert time.monotonic() - started < 0.8

    def test_slow_check_times_out(self, service: HealthService):
        release = threading.Event()
        service.register_check(
            "hung", lambda: release.wait(5) and OK, critical=True, timeout=0.1
        )
        service.register_check("fast", lambda: OK, critical=False)

        started = time.monotonic()
        body, status = service.check_readyz()
        elapsed = time.monotonic() - started
        release.set()

        assert status == 503
        assert body["checks"]["hung"] == {
            "status": "error", "critical": True, "detail": "timed out after 0.1 s",
        }
        assert body["checks"]["fast"]["status"] == "ok"
        assert elapsed < 1.0

    def test_a_running_check_is_joined_not_restarted(self, service: HealthService):
        release = threading.Event()
        calls: list[int] = []

        def hung() -> CheckResult:
            calls.append(1)
            release.wait(5)
            return OK

        service.register_check("hung", hung, critical=True, timeout=0.05)

        assert service.check_readyz()[1] == 503
        assert service.check_readyz()[1] == 503
        assert calls == [1]

        release.set()
        deadline = time.monotonic() + 2
        while service.check_readyz()[1] != 200 and time.monotonic() < deadline:
            time.sleep(0.01)

        # The run that outlived its timeout landed in the cache.
        assert service.check_readyz()[1] == 200
        assert calls == [1]


class TestCaching:
    def test_passing_result_is_reused(self, service: HealthService):
        check, calls = _counting()
        service.register_check("db", check, critical=True)

        service.check_readyz()
        service.check_readyz()

        assert calls == [1]

    def test_passing_result_expires(
        self, service: HealthService, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(health_module, "CACHE_TTL_SECONDS", 0.0)
        check, calls = _counting()
        service.register_check("db", check, critical=True)

        service.check_readyz()
        service.check_readyz()

        assert calls == [1, 1]

    def test_failing_result_is_not_cached(self, service: HealthService):
        results = [CheckResult(CheckStatus.ERROR, "down"), OK]
        service.register_check("db", lambda: results.pop(0), critical=True)

        assert service.check_readyz()[1] == 503
        assert service.check_readyz()[1] == 200

    def test_clear_cache_reruns_the_checks(self, service: HealthService):
        check, calls = _counting()
        service.register_check("db", check, critical=True)

        service.check_readyz()
        service.clear_cache()
        service.check_readyz()

        assert calls == [1, 1]


class TestObservability:
    def test_duration_is_recorded_per_run(self, service: HealthService):
        def count() -> float:
            for metric in READINESS_CHECK_DURATION_SECONDS.collect():
                for sample in metric.samples:
                    if sample.name.endswith("_count") and sample.labels == {
                        "check": "metered", "status": "ok",
                    }:
                        return sample.value
            return 0.0

        before = count()
        service.register_check("metered", lambda: OK, critical=True)

        service.check_readyz()
        service.check_readyz()  # cached: not a run

        assert count() - before == 1.0

    def test_failures_are_logged_on_change_only(
        self, service: HealthService, caplog: pytest.LogCaptureFixture
    ):
        results = [
            CheckResult(CheckStatus.ERROR, "down"),
            CheckResult(CheckStatus.ERROR, "down"),
            OK,
        ]
        service.register_check("db", lambda: results.pop(0), critical=True)

        with caplog.at_level(logging.INFO, logger="app.services.health_service"):
            for _ in range(3):
                service.check_readyz()

        messages = [record.getMessage() for record in caplog.records]
        assert messages == ["Readiness check db failed: down", "Readiness check db: ok"]


class TestHealthz:
    def test_always_alive(self, service: HealthService, coordinator: MagicMock):
        coordinator.is_shutting_down.return_value = True

        assert service.check_healthz() == ({"status": "alive", "ready": True}, 200)


class TestDrain:
    def test_no_key_configured(self, service: HealthService, coordinator: MagicMock):
        service.settings = service.settings.model_copy(update={"drain_auth_key": None})

        assert service.drain("Bearer anything")[1] == 401
        coordinator.shutdown.assert_not_called()

    def test_invalid_token(self, service: HealthService, coordinator: MagicMock):
        service.settings = service.settings.model_copy(update={"drain_auth_key": "key"})

        assert service.drain("Bearer wrong")[1] == 401
        coordinator.shutdown.assert_not_called()

    def test_valid_token_shuts_down(self, service: HealthService, coordinator: MagicMock):
        service.settings = service.settings.model_copy(update={"drain_auth_key": "key"})

        assert service.drain("Bearer key") == ({"status": "alive", "ready": True}, 200)
        coordinator.shutdown.assert_called_once()
