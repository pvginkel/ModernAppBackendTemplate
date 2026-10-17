"""The SSE Gateway connect callback admits only a caller the API would serve.

A refused connect answers 401 (no or invalid token) or 403 (a caller the API
refuses) before the connection is registered, so a refused stream never
receives an event. The gateway forwards the browser's headers in the callback
payload, so the tests put the token there, not in the test client's cookie
jar. Each test gets a fresh connection manager.
"""

from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from dependency_injector import providers
from flask import Flask
from flask.testing import FlaskClient

from app.services.auth_service import AuthService
from app.services.sse_connection_manager import (
    LOCAL_ADMIN_TARGET,
    SSEConnectionManager,
    Target,
)


def _connect_payload(request_id: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "action": "connect",
        "token": f"gw-{request_id}",
        "request": {
            "url": f"http://localhost:3002/api/sse/stream?request_id={request_id}",
            "headers": headers or {},
        },
    }


def _cookie(token: str) -> dict[str, str]:
    return {"cookie": f"theme=dark; access_token={token}"}


class _Callback:
    """The app under test, its fresh connection manager and a spy on `on_connect`."""

    def __init__(
        self,
        app: Flask,
        client: FlaskClient,
        manager: SSEConnectionManager,
        on_connect: MagicMock,
    ) -> None:
        self.app = app
        self.client = client
        self.manager = manager
        self.on_connect = on_connect

    def connect(self, request_id: str, headers: dict[str, str] | None = None) -> Any:
        return self.client.post("/api/sse/callback", json=_connect_payload(request_id, headers))

    def session_token(self, roles: list[str], subject: str = "session-user") -> str:
        return str(self.app.container.testing_service().create_session(subject, roles=roles))

    def subject_of(self, request_id: str) -> str | None:
        info = self.manager.get_connection_info(request_id)
        assert info is not None
        return info.subject

    def is_local_admin(self, request_id: str) -> bool:
        return request_id in self.manager._local_admin_ids

    def assert_refused(self, response: Any, status: int, request_id: str) -> None:
        assert response.status_code == status
        error = "Unauthorized" if status == 401 else "Forbidden"
        assert response.get_json() == {"error": error}
        self.on_connect.assert_not_called()
        assert not self.manager.has_connection(request_id)

    def task_event_recipients(self, target: Target) -> list[str]:
        """The gateway tokens a task event for this target is posted to."""
        with patch("app.services.sse_connection_manager.requests.post") as post:
            post.return_value = MagicMock(status_code=200)
            self.manager.send_event(target, {}, event_name="task_event", service_type="task")
        return sorted(call.kwargs["json"]["token"] for call in post.call_args_list)


def _callback(app: Flask, auth_service: AuthService | None = None) -> Generator[_Callback]:
    container = app.container
    testing_service = container.testing_service()
    manager = SSEConnectionManager(gateway_url="http://sse-gateway.invalid")
    container.sse_connection_manager.override(providers.Object(manager))
    if auth_service is not None:
        container.auth_service.override(providers.Object(auth_service))
    # The forced-error hook is class-level state: drain it around each test.
    testing_service.consume_forced_sse_callback_error()
    try:
        with patch.object(manager, "on_connect", wraps=manager.on_connect) as on_connect:
            yield _Callback(app, app.test_client(), manager, on_connect)
    finally:
        testing_service.consume_forced_sse_callback_error()
        container.sse_connection_manager.reset_override()
        if auth_service is not None:
            container.auth_service.reset_override()


@pytest.fixture
def oidc(roles_oidc_app: Flask) -> Generator[_Callback]:
    """OIDC on; ``editor`` gates writes, ``pipeline`` is an additional role, no read role."""
    yield from _callback(roles_oidc_app)


@pytest.fixture
def oidc_read_role(oidc_app: Flask) -> Generator[_Callback]:
    """OIDC on, with a read role: a caller needs ``reader`` or better, as for any GET."""
    auth_service = AuthService(
        config=oidc_app.container.config(),
        read_role="reader",
        write_role="editor",
        additional_roles=["pipeline"],
    )
    yield from _callback(oidc_app, auth_service)


@pytest.fixture
def local(app: Flask) -> Generator[_Callback]:
    """OIDC off, with roles configured, so test sessions are role-checked."""
    auth_service = AuthService(
        config=app.container.config(),
        write_role="editor",
        additional_roles=["pipeline"],
    )
    yield from _callback(app, auth_service)


class TestWithOidc:
    def test_no_token_is_refused_401(self, oidc: _Callback) -> None:
        oidc.assert_refused(oidc.connect("req-none"), 401, "req-none")

    @pytest.mark.parametrize(
        "flaw", ["expired", "invalid_signature", "invalid_issuer", "invalid_audience"]
    )
    def test_invalid_token_is_refused_401(
        self, oidc: _Callback, generate_test_jwt: Any, flaw: str
    ) -> None:
        token = generate_test_jwt(roles=["editor"], **{flaw: True})

        oidc.assert_refused(oidc.connect("req-bad", _cookie(token)), 401, "req-bad")

    @pytest.mark.parametrize("roles", [[], ["admin"], ["reader"]])
    def test_token_without_a_configured_role_is_refused_403(
        self, oidc: _Callback, generate_test_jwt: Any, roles: list[str]
    ) -> None:
        token = generate_test_jwt(roles=roles)

        oidc.assert_refused(oidc.connect("req-role", _cookie(token)), 403, "req-role")

    @pytest.mark.parametrize("carrier", ["cookie", "bearer"])
    def test_editor_token_is_accepted_and_bound(
        self, oidc: _Callback, generate_test_jwt: Any, carrier: str
    ) -> None:
        token = generate_test_jwt(subject="editor-user", roles=["editor"])
        headers = _cookie(token) if carrier == "cookie" else {"Authorization": f"Bearer {token}"}

        response = oidc.connect("req-ok", headers)

        assert response.status_code == 200
        assert response.get_json() == {}
        oidc.on_connect.assert_called_once()
        assert oidc.subject_of("req-ok") == "editor-user"
        assert not oidc.is_local_admin("req-ok")

    def test_additional_role_is_accepted(self, oidc: _Callback, generate_test_jwt: Any) -> None:
        token = generate_test_jwt(subject="pipeline-user", roles=["pipeline"])

        assert oidc.connect("req-pipe", _cookie(token)).status_code == 200
        assert oidc.subject_of("req-pipe") == "pipeline-user"

    def test_test_session_without_a_role_is_refused_403(self, oidc: _Callback) -> None:
        token = oidc.session_token(roles=["admin"])

        oidc.assert_refused(oidc.connect("req-ts", _cookie(token)), 403, "req-ts")

    def test_test_session_with_editor_is_accepted_and_bound(self, oidc: _Callback) -> None:
        token = oidc.session_token(roles=["editor"], subject="session-editor")

        response = oidc.connect("req-ts-ok", _cookie(token))

        assert response.status_code == 200
        assert oidc.subject_of("req-ts-ok") == "session-editor"

    def test_local_user_subject_is_not_the_local_admin(
        self, oidc: _Callback, generate_test_jwt: Any
    ) -> None:
        token = generate_test_jwt(subject="local-user", roles=["editor"])

        assert oidc.connect("req-lu", _cookie(token)).status_code == 200
        assert oidc.subject_of("req-lu") == "local-user"
        assert not oidc.is_local_admin("req-lu")


class TestReadRole:
    """Where the app sets a read role, the callback applies it as the API does to a GET."""

    def test_additional_role_alone_is_refused_403(
        self, oidc_read_role: _Callback, generate_test_jwt: Any
    ) -> None:
        token = generate_test_jwt(roles=["pipeline"])

        oidc_read_role.assert_refused(
            oidc_read_role.connect("req-pipe", _cookie(token)), 403, "req-pipe"
        )

    @pytest.mark.parametrize("role", ["reader", "editor"])
    def test_read_role_or_better_is_accepted(
        self, oidc_read_role: _Callback, generate_test_jwt: Any, role: str
    ) -> None:
        token = generate_test_jwt(roles=[role])

        assert oidc_read_role.connect("req-read", _cookie(token)).status_code == 200


class TestWithoutOidc:
    def test_no_session_is_accepted_as_the_local_admin(self, local: _Callback) -> None:
        response = local.connect("req-local")

        assert response.status_code == 200
        assert response.get_json() == {}
        assert local.subject_of("req-local") == "local-user"
        assert local.is_local_admin("req-local")

    def test_test_session_without_a_role_is_refused_403(self, local: _Callback) -> None:
        token = local.session_token(roles=[])

        local.assert_refused(local.connect("req-ts", _cookie(token)), 403, "req-ts")

    def test_test_session_with_editor_is_accepted_and_bound(self, local: _Callback) -> None:
        token = local.session_token(roles=["editor"], subject="session-editor")

        response = local.connect("req-ts-ok", _cookie(token))

        assert response.status_code == 200
        assert local.subject_of("req-ts-ok") == "session-editor"
        assert not local.is_local_admin("req-ts-ok")

    def test_unknown_session_token_is_accepted_as_the_local_admin(
        self, local: _Callback
    ) -> None:
        response = local.connect("req-stale", _cookie("test-session-gone"))

        assert response.status_code == 200
        assert local.subject_of("req-stale") == "local-user"
        assert local.is_local_admin("req-stale")


class TestTaskEventDelivery:
    """MAT-10: task events reach their owner, and nobody else, through the callback's binding."""

    def test_with_oidc_a_task_without_subject_reaches_nobody(
        self, oidc: _Callback, generate_test_jwt: Any
    ) -> None:
        for subject in ("alice", "local-user"):
            token = generate_test_jwt(subject=subject, roles=["editor"])
            assert oidc.connect(f"req-{subject}", _cookie(token)).status_code == 200

        assert oidc.task_event_recipients(LOCAL_ADMIN_TARGET) == []
        assert oidc.task_event_recipients(Target.subject("bob")) == []
        assert oidc.task_event_recipients(Target.subject("alice")) == ["gw-req-alice"]

    def test_without_oidc_the_local_admin_sees_every_task(self, local: _Callback) -> None:
        assert local.connect("req-admin").status_code == 200
        token = local.session_token(roles=["editor"], subject="session-editor")
        assert local.connect("req-session", _cookie(token)).status_code == 200

        assert local.task_event_recipients(LOCAL_ADMIN_TARGET) == ["gw-req-admin"]
        assert local.task_event_recipients(Target.subject("session-editor")) == [
            "gw-req-admin",
            "gw-req-session",
        ]
        assert local.task_event_recipients(Target.subject("bob")) == ["gw-req-admin"]


class TestForcedRejectionHook:
    """The testing-only single-shot hook refuses the next connect, whoever it is."""

    @pytest.mark.parametrize("status", [401, 403])
    def test_armed_hook_refuses_an_editor(
        self, oidc: _Callback, generate_test_jwt: Any, status: int
    ) -> None:
        oidc.app.container.testing_service().set_forced_sse_callback_error(status)
        token = generate_test_jwt(roles=["editor"])

        oidc.assert_refused(oidc.connect("req-forced", _cookie(token)), status, "req-forced")

    def test_armed_hook_refuses_the_local_admin(self, local: _Callback) -> None:
        local.app.container.testing_service().set_forced_sse_callback_error(403)

        local.assert_refused(local.connect("req-forced"), 403, "req-forced")

    def test_hook_is_consumed_once(self, oidc: _Callback, generate_test_jwt: Any) -> None:
        oidc.app.container.testing_service().set_forced_sse_callback_error(401)
        token = generate_test_jwt(subject="editor-user", roles=["editor"])

        assert oidc.connect("req-once-1", _cookie(token)).status_code == 401
        assert oidc.connect("req-once-2", _cookie(token)).status_code == 200
        assert oidc.subject_of("req-once-2") == "editor-user"

    def test_endpoint_arms_the_hook(self, local: _Callback) -> None:
        armed = local.client.post("/api/testing/sse/force-callback-error?status=403")
        assert armed.status_code == 204

        local.assert_refused(local.connect("req-armed"), 403, "req-armed")

    @pytest.mark.parametrize("status", ["500", "400", "abc"])
    def test_endpoint_rejects_other_statuses(self, local: _Callback, status: str) -> None:
        armed = local.client.post(f"/api/testing/sse/force-callback-error?status={status}")
        assert armed.status_code == 400

        assert local.connect("req-unarmed").status_code == 200

    def test_hook_is_ignored_outside_testing_mode(self, local: _Callback) -> None:
        local.app.container.testing_service().set_forced_sse_callback_error(401)
        settings = local.app.container.config()

        with patch.object(type(settings), "is_testing", new=False):
            assert local.connect("req-prod").status_code == 200

        # Still armed: the callback never consumed it
        assert local.app.container.testing_service().consume_forced_sse_callback_error() == 401


def test_disconnect_unregisters_the_connection(local: _Callback) -> None:
    assert local.connect("req-gone").status_code == 200

    response = local.client.post(
        "/api/sse/callback",
        json={
            "action": "disconnect",
            "reason": "client_closed",
            "token": "gw-req-gone",
            "request": _connect_payload("req-gone")["request"],
        },
    )

    assert response.status_code == 200
    assert response.get_json() == {}
    assert not local.manager.has_connection("req-gone")
    assert not local.is_local_admin("req-gone")
