"""The browser's SSE stream, GET /api/sse/stream (app/api/sse.py).

Every answer is a ``200`` stream: an accepted one starts with ``ready``, a
refused one carries one ``rejected`` event and ends, before the connection is
registered. The access token is the cookie; an ``EventSource`` sends no
headers. Each test gets a fresh connection manager, and reads the stream a
frame at a time through Flask's test client (``buffered=False``), which
pulls the first frame before ``get`` returns. The test settings make the
heartbeat interval 1 s, so a read on an idle stream waits that long.
"""

from collections.abc import Generator, Iterator
from contextlib import contextmanager
from queue import Empty
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from dependency_injector import providers
from flask import Flask
from flask.testing import FlaskClient
from werkzeug.test import TestResponse

from app.exceptions import AuthenticationException
from app.services.auth_service import AuthService
from app.services.oidc_client_service import TokenResponse
from app.services.sse_connection_manager import (
    LOCAL_ADMIN_TARGET,
    SSEConnection,
    SSEConnectionManager,
    Target,
)
from tests.testing_utils import TestLifecycleCoordinator

READY = "event: ready\ndata:\n\n"
HEARTBEAT = ": heartbeat\n\n"


def _rejected(status: int, message: str) -> str:
    return f'event: rejected\ndata: {{"status":{status},"message":"{message}"}}\n\n'


_REJECTED = {
    400: _rejected(400, "BAD REQUEST"),
    401: _rejected(401, "UNAUTHORIZED"),
    403: _rejected(403, "FORBIDDEN"),
}


class _Stream:
    """An open stream, read a frame at a time."""

    def __init__(self, response: TestResponse) -> None:
        self.response = response
        self._chunks: Iterator[bytes] = iter(response.response)

    def frame(self) -> str:
        """The next frame; a heartbeat when nothing arrives within the interval."""
        return next(self._chunks).decode()

    def assert_ended(self) -> None:
        with pytest.raises(StopIteration):
            next(self._chunks)


class _Streams:
    """The app under test, its fresh connection manager, and the connections it made."""

    def __init__(self, app: Flask, manager: SSEConnectionManager) -> None:
        self.app = app
        self.client = app.test_client()
        self.manager = manager
        self.on_connect = MagicMock(wraps=manager.on_connect)
        self.connections: dict[str, SSEConnection] = {}

        def recording_on_connect(
            request_id: str, subject: str | None, *, local_admin: bool = False
        ) -> SSEConnection:
            connection: SSEConnection = self.on_connect(
                request_id, subject, local_admin=local_admin
            )
            self.connections[request_id] = connection
            return connection

        manager.on_connect = recording_on_connect  # type: ignore[method-assign]

    @contextmanager
    def open(
        self,
        request_id: str | None,
        token: str | None = None,
        headers: dict[str, str] | None = None,
        client: FlaskClient | None = None,
    ) -> Iterator[_Stream]:
        """Open a stream; closing it on exit is the browser going."""
        client = client or self.client
        if token is None:
            client.delete_cookie("access_token")
        else:
            client.set_cookie("theme", "dark")
            client.set_cookie("access_token", token)
        url = "/api/sse/stream" if request_id is None else f"/api/sse/stream?request_id={request_id}"
        response = client.get(url, headers=headers or {}, buffered=False)
        assert response.status_code == 200
        assert response.mimetype == "text/event-stream"
        try:
            yield _Stream(response)
        finally:
            response.close()

    def accept(self, request_id: str, token: str | None = None) -> None:
        """Connect, see ``ready``, and go."""
        with self.open(request_id, token) as stream:
            assert stream.frame() == READY
            assert self.manager.has_connection(request_id)

    def bound(self, request_id: str, token: str | None = None) -> tuple[str | None, bool]:
        """Connect; the subject and local-admin flag the connection was bound to."""
        with self.open(request_id, token) as stream:
            assert stream.frame() == READY
            info = self.manager.get_connection_info(request_id)
            assert info is not None
            return info.subject, info.local_admin

    def assert_refused(self, status: int, request_id: str | None, token: str | None = None) -> None:
        """Refused in-stream with the status, and never registered."""
        with self.open(request_id, token) as stream:
            assert stream.frame() == _REJECTED[status]
            stream.assert_ended()
        self.on_connect.assert_not_called()
        if request_id is not None:
            assert not self.manager.has_connection(request_id)

    def session_token(self, roles: list[str], subject: str = "session-user") -> str:
        return str(self.app.container.testing_service().create_session(subject, roles=roles))

    def recipients(self, target: Target) -> list[str]:
        """The request IDs a task event for this target is queued for."""
        self.manager.send_event(target, {}, event_name="task_event", service_type="task")
        reached = []
        for request_id, connection in self.connections.items():
            try:
                if connection.next_frame(0) is not None:
                    reached.append(request_id)
            except Empty:
                pass
        return sorted(reached)


def _streams(
    app: Flask,
    auth_service: AuthService | None = None,
    manager: SSEConnectionManager | None = None,
) -> Generator[_Streams]:
    container = app.container
    testing_service = container.testing_service()
    manager = manager or SSEConnectionManager(TestLifecycleCoordinator())
    container.sse_connection_manager.override(providers.Object(manager))
    if auth_service is not None:
        container.auth_service.override(providers.Object(auth_service))
    # The forced-error hook is class-level state: drain it around each test.
    testing_service.consume_forced_sse_connect_error()
    try:
        yield _Streams(app, manager)
    finally:
        testing_service.consume_forced_sse_connect_error()
        container.sse_connection_manager.reset_override()
        if auth_service is not None:
            container.auth_service.reset_override()


@pytest.fixture
def oidc(roles_oidc_app: Flask) -> Generator[_Streams]:
    """OIDC on; ``editor`` gates writes, ``pipeline`` is an additional role, no read role."""
    yield from _streams(roles_oidc_app)


@pytest.fixture
def oidc_read_role(oidc_app: Flask) -> Generator[_Streams]:
    """OIDC on, with a read role: a caller needs ``reader`` or better, as for any GET."""
    auth_service = AuthService(
        config=oidc_app.container.config(),
        read_role="reader",
        write_role="editor",
        additional_roles=["pipeline"],
    )
    yield from _streams(oidc_app, auth_service)


def _local_auth_service(app: Flask) -> AuthService:
    """Roles configured, so test sessions are role-checked."""
    return AuthService(
        config=app.container.config(),
        write_role="editor",
        additional_roles=["pipeline"],
    )


@pytest.fixture
def local(app: Flask) -> Generator[_Streams]:
    """OIDC off, with roles configured, so test sessions are role-checked."""
    yield from _streams(app, _local_auth_service(app))


@pytest.fixture
def lifecycle() -> TestLifecycleCoordinator:
    return TestLifecycleCoordinator()


@pytest.fixture
def local_capped(app: Flask, lifecycle: TestLifecycleCoordinator) -> Generator[_Streams]:
    """As ``local``, with a manager whose queues hold two frames and whose lifecycle a test drives."""
    manager = SSEConnectionManager(lifecycle, queue_max_frames=2)
    yield from _streams(app, _local_auth_service(app), manager)


class TestWithOidc:
    def test_no_token_is_refused_401(self, oidc: _Streams) -> None:
        oidc.assert_refused(401, "req-none")

    @pytest.mark.parametrize(
        "flaw", ["expired", "invalid_signature", "invalid_issuer", "invalid_audience"]
    )
    def test_invalid_token_is_refused_401(
        self, oidc: _Streams, generate_test_jwt: Any, flaw: str
    ) -> None:
        token = generate_test_jwt(roles=["editor"], **{flaw: True})

        oidc.assert_refused(401, "req-bad", token)

    @pytest.mark.parametrize("roles", [[], ["admin"], ["reader"]])
    def test_token_without_a_configured_role_is_refused_403(
        self, oidc: _Streams, generate_test_jwt: Any, roles: list[str]
    ) -> None:
        token = generate_test_jwt(roles=roles)

        oidc.assert_refused(403, "req-role", token)

    def test_editor_token_is_accepted_and_bound(
        self, oidc: _Streams, generate_test_jwt: Any
    ) -> None:
        token = generate_test_jwt(subject="editor-user", roles=["editor"])

        assert oidc.bound("req-ok", token) == ("editor-user", False)
        oidc.on_connect.assert_called_once()

    def test_a_bearer_header_is_not_read(self, oidc: _Streams, generate_test_jwt: Any) -> None:
        """An EventSource sends no headers; the cookie is the token."""
        token = generate_test_jwt(roles=["editor"])

        with oidc.open("req-bearer", headers={"Authorization": f"Bearer {token}"}) as stream:
            assert stream.frame() == _REJECTED[401]

    def test_additional_role_is_accepted(self, oidc: _Streams, generate_test_jwt: Any) -> None:
        token = generate_test_jwt(subject="pipeline-user", roles=["pipeline"])

        assert oidc.bound("req-pipe", token) == ("pipeline-user", False)

    def test_test_session_without_a_role_is_refused_403(self, oidc: _Streams) -> None:
        oidc.assert_refused(403, "req-ts", oidc.session_token(roles=["admin"]))

    def test_test_session_with_editor_is_accepted_and_bound(self, oidc: _Streams) -> None:
        token = oidc.session_token(roles=["editor"], subject="session-editor")

        assert oidc.bound("req-ts-ok", token) == ("session-editor", False)

    def test_local_user_subject_is_not_the_local_admin(
        self, oidc: _Streams, generate_test_jwt: Any
    ) -> None:
        token = generate_test_jwt(subject="local-user", roles=["editor"])

        assert oidc.bound("req-lu", token) == ("local-user", False)

    def test_an_expired_token_is_refreshed_and_accepted(
        self, oidc: _Streams, generate_test_jwt: Any
    ) -> None:
        """The gate refreshes it: the stream is accepted at once, as the new identity.

        The new cookies go out on the stream's response, so the browser needs
        no session probe and no reopen.
        """
        fresh = TokenResponse(
            access_token=generate_test_jwt(subject="refreshed-user", roles=["editor"]),
            id_token=None,
            refresh_token=None,
            token_type="Bearer",
            expires_in=300,
        )
        oidc.client.set_cookie("refresh_token", "old-refresh")

        with (
            patch.object(
                oidc.app.container.oidc_client_service(),
                "refresh_access_token",
                return_value=fresh,
            ) as refresh,
            oidc.open("req-refresh", generate_test_jwt(roles=["editor"], expired=True)) as stream,
        ):
            assert stream.frame() == READY
            info = oidc.manager.get_connection_info("req-refresh")
            set_cookies = stream.response.headers.getlist("Set-Cookie")

        refresh.assert_called_once_with("old-refresh")
        assert info is not None
        assert (info.subject, info.local_admin) == ("refreshed-user", False)
        assert any(c.startswith(f"access_token={fresh.access_token};") for c in set_cookies)

    def test_an_expired_token_whose_refresh_fails_is_refused_401(
        self, oidc: _Streams, generate_test_jwt: Any
    ) -> None:
        """Refused in-stream, and the gate clears the auth cookies on the response."""
        oidc.client.set_cookie("refresh_token", "spent-refresh")

        with (
            patch.object(
                oidc.app.container.oidc_client_service(),
                "refresh_access_token",
                side_effect=AuthenticationException("Refresh failed"),
            ),
            oidc.open("req-spent", generate_test_jwt(roles=["editor"], expired=True)) as stream,
        ):
            assert stream.frame() == _REJECTED[401]
            stream.assert_ended()
            set_cookies = stream.response.headers.getlist("Set-Cookie")

        oidc.on_connect.assert_not_called()
        assert any(c.startswith("access_token=;") and "Max-Age=0" in c for c in set_cookies)


class TestReadRole:
    """Where the app sets a read role, the stream applies it as the API does to a GET."""

    def test_additional_role_alone_is_refused_403(
        self, oidc_read_role: _Streams, generate_test_jwt: Any
    ) -> None:
        token = generate_test_jwt(roles=["pipeline"])

        oidc_read_role.assert_refused(403, "req-pipe", token)

    @pytest.mark.parametrize("role", ["reader", "editor"])
    def test_read_role_or_better_is_accepted(
        self, oidc_read_role: _Streams, generate_test_jwt: Any, role: str
    ) -> None:
        oidc_read_role.accept("req-read", generate_test_jwt(roles=[role]))


class TestWithoutOidc:
    def test_no_session_is_accepted_as_the_local_admin(self, local: _Streams) -> None:
        assert local.bound("req-local") == ("local-user", True)

    def test_test_session_without_a_role_is_refused_403(self, local: _Streams) -> None:
        local.assert_refused(403, "req-ts", local.session_token(roles=[]))

    def test_test_session_with_editor_is_accepted_and_bound(self, local: _Streams) -> None:
        token = local.session_token(roles=["editor"], subject="session-editor")

        assert local.bound("req-ts-ok", token) == ("session-editor", False)

    def test_unknown_session_token_is_accepted_as_the_local_admin(
        self, local: _Streams
    ) -> None:
        assert local.bound("req-stale", "test-session-gone") == ("local-user", True)


class TestRequestId:
    def test_missing_is_refused_400(self, local: _Streams) -> None:
        local.assert_refused(400, None)

    def test_empty_is_refused_400(self, local: _Streams) -> None:
        local.assert_refused(400, "")

    def test_a_colon_is_refused_400(self, local: _Streams) -> None:
        local.assert_refused(400, "req:1")


class TestStream:
    def test_headers(self, local: _Streams) -> None:
        with local.open("req-1") as stream:
            headers = stream.response.headers
            assert headers["Content-Type"] == "text/event-stream; charset=utf-8"
            assert headers["Cache-Control"] == "no-cache"
            assert headers["X-Accel-Buffering"] == "no"
            assert "Content-Encoding" not in headers

    def test_ready_then_an_event_sent_to_the_connection(self, local: _Streams) -> None:
        with local.open("req-1") as stream:
            assert stream.frame() == READY

            assert local.manager.send_event(
                Target.connection("req-1"),
                {"task_id": "t1"},
                event_name="task_event",
                service_type="task",
            )

            assert stream.frame() == 'data: {"type":"task_event","payload":{"task_id": "t1"}}\n\n'

    def test_heartbeat_when_idle(self, local: _Streams) -> None:
        with local.open("req-1") as stream:
            assert stream.frame() == READY
            assert stream.frame() == HEARTBEAT
            assert local.manager.has_connection("req-1")

    def test_connect_observers_see_the_bound_connection(self, local: _Streams) -> None:
        """A connect observer reaches the new connection through its subject; ready goes first."""
        local.manager.register_on_connect(
            lambda request_id: local.manager.send_event(
                Target.subject("someone"), {"hello": request_id}, "greeting", "test"
            )
        )

        with local.open("req-1") as stream:
            assert stream.frame() == READY
            assert stream.frame() == 'data: {"type":"greeting","payload":{"hello": "req-1"}}\n\n'

    def test_the_browser_going_unregisters_and_notifies(self, local: _Streams) -> None:
        disconnected = MagicMock()
        local.manager.register_on_disconnect(disconnected)

        with local.open("req-1") as stream:
            assert stream.frame() == READY
            disconnected.assert_not_called()

        assert not local.manager.has_connection("req-1")
        disconnected.assert_called_once_with("req-1")

    def test_a_new_stream_replaces_the_old(self, local: _Streams) -> None:
        disconnected = MagicMock()
        local.manager.register_on_disconnect(disconnected)

        with local.open("req-1") as old:
            assert old.frame() == READY
            with local.open("req-1", client=local.app.test_client()) as new:
                assert new.frame() == READY

                old.assert_ended()
                assert local.manager.has_connection("req-1")
                disconnected.assert_not_called()

                assert local.manager.send_event(
                    Target.connection("req-1"), {}, "task_event", "task"
                )
                assert new.frame() == 'data: {"type":"task_event","payload":{}}\n\n'

        assert not local.manager.has_connection("req-1")
        disconnected.assert_called_once_with("req-1")

    def test_a_full_queue_ends_the_stream(self, local_capped: _Streams) -> None:
        disconnected = MagicMock()
        local_capped.manager.register_on_disconnect(disconnected)

        with local_capped.open("req-1") as stream:
            assert stream.frame() == READY
            for _ in range(3):
                local_capped.manager.send_event(
                    Target.connection("req-1"), {}, "task_event", "task"
                )

            assert not local_capped.manager.has_connection("req-1")
            disconnected.assert_called_once_with("req-1")
            stream.assert_ended()

    def test_prepare_shutdown_ends_the_stream(
        self, local_capped: _Streams, lifecycle: TestLifecycleCoordinator
    ) -> None:
        with local_capped.open("req-1") as stream:
            assert stream.frame() == READY

            lifecycle.simulate_shutdown()

            stream.assert_ended()
            assert not local_capped.manager.has_connection("req-1")

        # A browser reconnecting meanwhile gets a stream that ends without ready
        with local_capped.open("req-1") as stream:
            stream.assert_ended()
        assert not local_capped.manager.has_connection("req-1")

    def test_a_head_request_leaves_no_connection(self, local: _Streams) -> None:
        response = local.client.head("/api/sse/stream?request_id=req-1")

        assert response.status_code == 200
        response.close()
        assert not local.manager.has_connection("req-1")


class TestSubscriptions:
    def test_channel_events_reach_a_subscribed_stream(self, local: _Streams) -> None:
        with local.open("req-1") as stream:
            assert stream.frame() == READY
            assert local.manager.subscribe("req-1", "news")

            assert local.manager.send_event(Target.channel("news"), {"n": 1}, "news", "test")

            assert stream.frame() == 'data: {"type":"news","payload":{"n": 1}}\n\n'

    def test_a_subscription_goes_with_its_stream(self, local: _Streams) -> None:
        with local.open("req-1") as stream:
            assert stream.frame() == READY
            local.manager.subscribe("req-1", "news")

        assert not local.manager.send_event(Target.channel("news"), {}, "news", "test")
        assert not local.manager.subscribe("req-1", "news")


class TestTaskEventDelivery:
    """MAT-10: task events reach their owner, and nobody else, through the stream's binding."""

    def test_with_oidc_a_task_without_subject_reaches_nobody(
        self, oidc: _Streams, generate_test_jwt: Any
    ) -> None:
        alice = generate_test_jwt(subject="alice", roles=["editor"])
        local_user = generate_test_jwt(subject="local-user", roles=["editor"])
        with (
            oidc.open("req-alice", alice) as s1,
            oidc.open("req-local-user", local_user, client=oidc.app.test_client()) as s2,
        ):
            assert s1.frame() == s2.frame() == READY

            assert oidc.recipients(LOCAL_ADMIN_TARGET) == []
            assert oidc.recipients(Target.subject("bob")) == []
            assert oidc.recipients(Target.subject("alice")) == ["req-alice"]

    def test_without_oidc_the_local_admin_sees_every_task(self, local: _Streams) -> None:
        token = local.session_token(roles=["editor"], subject="session-editor")
        with (
            local.open("req-admin") as s1,
            local.open("req-session", token, client=local.app.test_client()) as s2,
        ):
            assert s1.frame() == s2.frame() == READY

            assert local.recipients(LOCAL_ADMIN_TARGET) == ["req-admin"]
            assert local.recipients(Target.subject("session-editor")) == [
                "req-admin",
                "req-session",
            ]
            assert local.recipients(Target.subject("bob")) == ["req-admin"]


class TestForcedRejectionHook:
    """The testing-only single-shot hook refuses the next connect, whoever it is."""

    @pytest.mark.parametrize("status", [401, 403])
    def test_armed_hook_refuses_an_editor(
        self, oidc: _Streams, generate_test_jwt: Any, status: int
    ) -> None:
        oidc.app.container.testing_service().set_forced_sse_connect_error(status)

        oidc.assert_refused(status, "req-forced", generate_test_jwt(roles=["editor"]))

    def test_armed_hook_refuses_the_local_admin(self, local: _Streams) -> None:
        local.app.container.testing_service().set_forced_sse_connect_error(403)

        local.assert_refused(403, "req-forced")

    def test_hook_runs_before_the_identity_check(self, oidc: _Streams) -> None:
        """No token would be a 401; the armed 403 answers first."""
        oidc.app.container.testing_service().set_forced_sse_connect_error(403)

        oidc.assert_refused(403, "req-forced")

    def test_hook_is_consumed_once(self, oidc: _Streams, generate_test_jwt: Any) -> None:
        oidc.app.container.testing_service().set_forced_sse_connect_error(401)
        token = generate_test_jwt(subject="editor-user", roles=["editor"])

        oidc.assert_refused(401, "req-once-1", token)
        assert oidc.bound("req-once-2", token) == ("editor-user", False)

    def test_endpoint_arms_the_hook(self, local: _Streams) -> None:
        armed = local.client.post("/api/testing/sse/force-callback-error?status=403")
        assert armed.status_code == 204

        local.assert_refused(403, "req-armed")

    @pytest.mark.parametrize("status", ["500", "400", "abc"])
    def test_endpoint_rejects_other_statuses(self, local: _Streams, status: str) -> None:
        armed = local.client.post(f"/api/testing/sse/force-callback-error?status={status}")
        assert armed.status_code == 400

        local.accept("req-unarmed")

    def test_hook_is_ignored_outside_testing_mode(self, local: _Streams) -> None:
        local.app.container.testing_service().set_forced_sse_connect_error(401)
        settings = local.app.container.config()

        with patch.object(type(settings), "is_testing", new=False):
            local.accept("req-prod")

        # Still armed: the stream never consumed it
        assert local.app.container.testing_service().consume_forced_sse_connect_error() == 401
