"""Tests for authenticate_request: throwing and tolerant (``@public``) modes."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest
from flask import Flask, g

from app.exceptions import AuthenticationException, AuthorizationException
from app.services.auth_service import AuthContext
from app.services.oidc_client_service import TokenResponse
from app.services.testing_service import TestSession as SessionRecord
from app.utils.auth import authenticate_request

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"


@pytest.fixture
def flask_app() -> Flask:
    return Flask(__name__)


def _make_config(*, is_testing: bool = False, oidc_enabled: bool = True) -> MagicMock:
    config = MagicMock()
    config.is_testing = is_testing
    config.oidc_enabled = oidc_enabled
    config.oidc_cookie_name = ACCESS_COOKIE
    config.oidc_refresh_cookie_name = REFRESH_COOKIE
    return config


def _make_auth_service(
    *,
    validate_token_result: AuthContext | None = None,
    validate_token_side_effect: Any = None,
    configured_roles: set[str] | None = None,
    required_role: str | None = None,
) -> MagicMock:
    auth_service = MagicMock()
    if validate_token_side_effect is not None:
        auth_service.validate_token.side_effect = validate_token_side_effect
    else:
        auth_service.validate_token.return_value = validate_token_result
    auth_service.expand_roles.side_effect = lambda roles: set(roles)
    auth_service.configured_roles = (
        configured_roles if configured_roles is not None else {"editor", "pipeline"}
    )
    auth_service.resolve_required_role.return_value = required_role
    return auth_service


def _make_oidc_client(
    *, refresh_result: TokenResponse | None = None, refresh_error: Exception | None = None
) -> MagicMock:
    oidc_client = MagicMock()
    if refresh_error is not None:
        oidc_client.refresh_access_token.side_effect = refresh_error
    else:
        oidc_client.refresh_access_token.return_value = refresh_result
    return oidc_client


@contextmanager
def _request(
    flask_app: Flask, cookies: dict[str, str] | None = None, bearer: str | None = None
) -> Iterator[None]:
    headers: list[tuple[str, str]] = []
    if cookies:
        headers.append(("Cookie", "; ".join(f"{k}={v}" for k, v in cookies.items())))
    if bearer:
        headers.append(("Authorization", f"Bearer {bearer}"))
    with flask_app.test_request_context(headers=headers):
        yield


def _ctx(*roles: str) -> AuthContext:
    return AuthContext(subject="user-1", email="user@example.com", name="User", roles=set(roles))


def _fresh_tokens() -> TokenResponse:
    return TokenResponse(
        access_token="new-access",
        id_token=None,
        refresh_token="new-refresh",
        token_type="Bearer",
        expires_in=300,
    )


def _assert_nothing_on_g() -> None:
    assert not hasattr(g, "auth_context")
    assert not hasattr(g, "pending_token_refresh")
    assert not hasattr(g, "clear_auth_cookies")


class TestTolerantMode:
    """``throw_on_error=False``: authentication failures return silently."""

    def test_no_tokens(self, flask_app: Flask) -> None:
        auth_service = _make_auth_service()
        oidc_client = _make_oidc_client()

        with _request(flask_app):
            authenticate_request(
                auth_service, _make_config(), "GET", oidc_client, None, throw_on_error=False
            )
            _assert_nothing_on_g()

        oidc_client.refresh_access_token.assert_not_called()

    def test_expired_access_without_refresh_cookie(self, flask_app: Flask) -> None:
        auth_service = _make_auth_service(
            validate_token_side_effect=AuthenticationException("Token has expired")
        )
        oidc_client = _make_oidc_client()

        with _request(flask_app, cookies={ACCESS_COOKIE: "expired-jwt"}):
            authenticate_request(
                auth_service, _make_config(), "GET", oidc_client, None, throw_on_error=False
            )
            _assert_nothing_on_g()

        oidc_client.refresh_access_token.assert_not_called()

    def test_expired_access_with_valid_refresh(self, flask_app: Flask) -> None:
        fresh = _ctx("editor")
        auth_service = _make_auth_service(
            validate_token_side_effect=[AuthenticationException("Token has expired"), fresh]
        )
        oidc_client = _make_oidc_client(refresh_result=_fresh_tokens())

        with _request(
            flask_app, cookies={ACCESS_COOKIE: "expired-jwt", REFRESH_COOKIE: "valid-refresh"}
        ):
            authenticate_request(
                auth_service, _make_config(), "GET", oidc_client, None, throw_on_error=False
            )

            assert g.auth_context is fresh
            assert g.pending_token_refresh.access_token == "new-access"
            assert g.pending_token_refresh.refresh_token == "new-refresh"
            assert g.pending_token_refresh.access_token_expires_in == 300
            assert not hasattr(g, "clear_auth_cookies")

        oidc_client.refresh_access_token.assert_called_once_with("valid-refresh")

    def test_expired_access_with_failed_refresh_clears_cookies(self, flask_app: Flask) -> None:
        auth_service = _make_auth_service(
            validate_token_side_effect=AuthenticationException("Token has expired")
        )
        oidc_client = _make_oidc_client(refresh_error=AuthenticationException("Refresh failed"))

        with _request(
            flask_app, cookies={ACCESS_COOKIE: "expired-jwt", REFRESH_COOKIE: "expired-refresh"}
        ):
            authenticate_request(
                auth_service, _make_config(), "GET", oidc_client, None, throw_on_error=False
            )

            assert g.clear_auth_cookies is True
            assert not hasattr(g, "auth_context")
            assert not hasattr(g, "pending_token_refresh")

    def test_valid_access_token(self, flask_app: Flask) -> None:
        ctx = _ctx("editor")
        auth_service = _make_auth_service(validate_token_result=ctx)

        with _request(flask_app, cookies={ACCESS_COOKIE: "valid-jwt"}):
            authenticate_request(
                auth_service, _make_config(), "GET", None, None, throw_on_error=False
            )

            assert g.auth_context is ctx
            assert not hasattr(g, "pending_token_refresh")
            assert not hasattr(g, "clear_auth_cookies")

    def test_authorization_failure_propagates(self, flask_app: Flask) -> None:
        ctx = _ctx("random-role")
        auth_service = _make_auth_service(validate_token_result=ctx, required_role="editor")

        with _request(flask_app, cookies={ACCESS_COOKIE: "valid-jwt"}):
            with pytest.raises(AuthorizationException, match="No recognized role"):
                authenticate_request(
                    auth_service, _make_config(), "GET", None, None, throw_on_error=False
                )

            assert g.auth_context is ctx

    def test_non_expiry_validation_failure(self, flask_app: Flask) -> None:
        auth_service = _make_auth_service(
            validate_token_side_effect=AuthenticationException("Invalid token signature")
        )
        oidc_client = _make_oidc_client(refresh_result=_fresh_tokens())

        with _request(
            flask_app, cookies={ACCESS_COOKIE: "bad-jwt", REFRESH_COOKIE: "valid-refresh"}
        ):
            authenticate_request(
                auth_service, _make_config(), "GET", oidc_client, None, throw_on_error=False
            )
            _assert_nothing_on_g()

        oidc_client.refresh_access_token.assert_not_called()

    def test_test_session(self, flask_app: Flask) -> None:
        auth_service = _make_auth_service()
        testing_service = MagicMock()
        testing_service.get_session.return_value = SessionRecord(
            subject="session-user", name="Session User", email="s@example.com", roles=["editor"]
        )

        with _request(flask_app, cookies={ACCESS_COOKIE: "test-session-abc"}):
            authenticate_request(
                auth_service,
                _make_config(is_testing=True),
                "GET",
                None,
                None,
                throw_on_error=False,
                testing_service=testing_service,
            )

            assert g.auth_context.subject == "session-user"
            assert g.auth_context.roles == {"editor"}

        auth_service.validate_token.assert_not_called()

    def test_bearer_token_is_not_read(self, flask_app: Flask) -> None:
        """A bearer token belongs to the public endpoint's own scheme."""
        auth_service = _make_auth_service(validate_token_result=_ctx())

        with _request(flask_app, bearer="device-jwt"):
            authenticate_request(
                auth_service, _make_config(), "POST", None, None, throw_on_error=False
            )
            _assert_nothing_on_g()

        auth_service.validate_token.assert_not_called()


class TestThrowingMode:
    """``throw_on_error=True``: authentication failures raise."""

    def test_no_tokens_raises(self, flask_app: Flask) -> None:
        with _request(flask_app):
            with pytest.raises(AuthenticationException, match="No valid token provided"):
                authenticate_request(
                    _make_auth_service(), _make_config(), "GET", None, None, throw_on_error=True
                )
            assert not hasattr(g, "clear_auth_cookies")

    def test_expired_access_without_refresh_cookie_raises(self, flask_app: Flask) -> None:
        auth_service = _make_auth_service(
            validate_token_side_effect=AuthenticationException("Token has expired")
        )

        with _request(flask_app, cookies={ACCESS_COOKIE: "expired-jwt"}):
            with pytest.raises(AuthenticationException, match="Token has expired"):
                authenticate_request(
                    auth_service, _make_config(), "GET", None, None, throw_on_error=True
                )
            assert not hasattr(g, "clear_auth_cookies")

    def test_failed_refresh_raises_and_clears_cookies(self, flask_app: Flask) -> None:
        auth_service = _make_auth_service(
            validate_token_side_effect=AuthenticationException("Token has expired")
        )
        oidc_client = _make_oidc_client(refresh_error=AuthenticationException("Refresh failed"))

        with _request(
            flask_app, cookies={ACCESS_COOKIE: "expired-jwt", REFRESH_COOKIE: "expired-refresh"}
        ):
            with pytest.raises(AuthenticationException, match="Session expired"):
                authenticate_request(
                    auth_service, _make_config(), "GET", oidc_client, None, throw_on_error=True
                )
            assert g.clear_auth_cookies is True

    def test_successful_refresh_matches_tolerant_mode(self, flask_app: Flask) -> None:
        fresh = _ctx("editor")
        auth_service = _make_auth_service(
            validate_token_side_effect=[AuthenticationException("Token has expired"), fresh]
        )
        oidc_client = _make_oidc_client(refresh_result=_fresh_tokens())

        with _request(
            flask_app, cookies={ACCESS_COOKIE: "expired-jwt", REFRESH_COOKIE: "valid-refresh"}
        ):
            authenticate_request(
                auth_service, _make_config(), "GET", oidc_client, None, throw_on_error=True
            )

            assert g.auth_context is fresh
            assert g.pending_token_refresh.access_token == "new-access"
            assert not hasattr(g, "clear_auth_cookies")

    def test_non_expiry_validation_failure_raises(self, flask_app: Flask) -> None:
        auth_service = _make_auth_service(
            validate_token_side_effect=AuthenticationException("Invalid token signature")
        )
        oidc_client = _make_oidc_client(refresh_result=_fresh_tokens())

        with _request(
            flask_app, cookies={ACCESS_COOKIE: "bad-jwt", REFRESH_COOKIE: "valid-refresh"}
        ):
            with pytest.raises(AuthenticationException, match="Invalid token signature"):
                authenticate_request(
                    auth_service, _make_config(), "GET", oidc_client, None, throw_on_error=True
                )
            _assert_nothing_on_g()

        oidc_client.refresh_access_token.assert_not_called()

    def test_bearer_token_is_read(self, flask_app: Flask) -> None:
        ctx = _ctx("pipeline")
        auth_service = _make_auth_service(validate_token_result=ctx)

        with _request(flask_app, bearer="pipeline-jwt"):
            authenticate_request(
                auth_service, _make_config(), "GET", None, None, throw_on_error=True
            )
            assert g.auth_context is ctx

        auth_service.validate_token.assert_called_once_with("pipeline-jwt")


class TestOidcDisabled:
    """With OIDC off every request is the local admin, exempt from role checks."""

    @pytest.mark.parametrize("throw_on_error", [True, False])
    def test_local_admin_without_role_checks(
        self, flask_app: Flask, throw_on_error: bool
    ) -> None:
        # "admin" is not a configured role, so a role check would refuse
        auth_service = _make_auth_service(configured_roles={"editor"}, required_role="editor")

        with _request(flask_app):
            authenticate_request(
                auth_service,
                _make_config(oidc_enabled=False),
                "POST",
                None,
                None,
                throw_on_error=throw_on_error,
            )

            assert g.auth_context.subject == "local-user"
            assert g.auth_context.roles == {"admin"}

        auth_service.resolve_required_role.assert_not_called()
        auth_service.validate_token.assert_not_called()

    def test_test_session_is_role_checked(self, flask_app: Flask) -> None:
        auth_service = _make_auth_service(configured_roles={"editor"})
        testing_service = MagicMock()
        testing_service.get_session.return_value = SessionRecord(
            subject="session-user", name=None, email=None, roles=[]
        )

        with _request(flask_app, cookies={ACCESS_COOKIE: "test-session-abc"}):
            with pytest.raises(AuthorizationException, match="No recognized role"):
                authenticate_request(
                    auth_service,
                    _make_config(is_testing=True, oidc_enabled=False),
                    "GET",
                    None,
                    None,
                    throw_on_error=True,
                    testing_service=testing_service,
                )
