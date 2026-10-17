"""Tests for resolve_auth_identity, the one identity-resolution path."""

from unittest.mock import MagicMock

import pytest

from app.exceptions import AuthenticationException
from app.services.auth_service import AuthContext
from app.services.testing_service import TestSession as SessionRecord
from app.utils.auth import resolve_auth_identity


def _make_config(*, is_testing: bool = False, oidc_enabled: bool = True) -> MagicMock:
    config = MagicMock()
    config.is_testing = is_testing
    config.oidc_enabled = oidc_enabled
    return config


def _make_auth_service(
    *,
    validate_token_result: AuthContext | None = None,
    validate_token_side_effect: Exception | None = None,
) -> MagicMock:
    auth_service = MagicMock()
    auth_service.expand_roles.side_effect = lambda roles: set(roles) | {"expanded"}
    if validate_token_side_effect is not None:
        auth_service.validate_token.side_effect = validate_token_side_effect
    else:
        auth_service.validate_token.return_value = validate_token_result
    return auth_service


def _make_testing_service(sessions: dict[str, SessionRecord] | None = None) -> MagicMock:
    known = sessions or {}
    testing_service = MagicMock()
    testing_service.get_session.side_effect = lambda token: known.get(token)
    return testing_service


def _session(roles: list[str] | None = None) -> SessionRecord:
    return SessionRecord(
        subject="session-user",
        name="Session User",
        email="session@example.com",
        roles=roles if roles is not None else ["editor"],
    )


class TestTestSessionPath:
    def test_valid_test_session(self) -> None:
        testing_service = _make_testing_service({"test-session-abc": _session()})
        auth_service = _make_auth_service()

        result = resolve_auth_identity(
            "test-session-abc", auth_service, _make_config(is_testing=True), testing_service
        )

        assert result is not None
        assert result.subject == "session-user"
        assert result.email == "session@example.com"
        assert result.name == "Session User"
        assert result.roles == {"editor", "expanded"}
        auth_service.expand_roles.assert_called_once_with({"editor"})
        auth_service.validate_token.assert_not_called()

    def test_unknown_test_session_falls_through_to_validation(self) -> None:
        auth_service = _make_auth_service(
            validate_token_side_effect=AuthenticationException("Invalid token")
        )

        with pytest.raises(AuthenticationException, match="Invalid token"):
            resolve_auth_identity(
                "test-session-gone",
                auth_service,
                _make_config(is_testing=True),
                _make_testing_service(),
            )

        auth_service.validate_token.assert_called_once_with("test-session-gone")

    def test_unknown_test_session_with_oidc_disabled_is_local_admin(self) -> None:
        result = resolve_auth_identity(
            "test-session-gone",
            _make_auth_service(),
            _make_config(is_testing=True, oidc_enabled=False),
            _make_testing_service(),
        )

        assert result is not None
        assert result.subject == "local-user"

    def test_token_without_prefix_skips_test_sessions(self) -> None:
        ctx = AuthContext(subject="jwt-user", email=None, name=None, roles=set())
        testing_service = _make_testing_service()
        auth_service = _make_auth_service(validate_token_result=ctx)

        result = resolve_auth_identity(
            "eyJ.jwt.token", auth_service, _make_config(is_testing=True), testing_service
        )

        assert result is ctx
        testing_service.get_session.assert_not_called()

    def test_not_testing_skips_test_sessions(self) -> None:
        ctx = AuthContext(subject="jwt-user", email=None, name=None, roles=set())
        testing_service = _make_testing_service({"test-session-abc": _session()})
        auth_service = _make_auth_service(validate_token_result=ctx)

        result = resolve_auth_identity(
            "test-session-abc", auth_service, _make_config(is_testing=False), testing_service
        )

        assert result is ctx
        testing_service.get_session.assert_not_called()

    def test_no_testing_service_skips_test_sessions(self) -> None:
        result = resolve_auth_identity(
            "test-session-abc",
            _make_auth_service(),
            _make_config(is_testing=True, oidc_enabled=False),
            None,
        )

        assert result is not None
        assert result.subject == "local-user"

    def test_test_session_roles_are_expanded(self) -> None:
        testing_service = _make_testing_service({"test-session-abc": _session(["admin"])})
        auth_service = _make_auth_service()

        result = resolve_auth_identity(
            "test-session-abc", auth_service, _make_config(is_testing=True), testing_service
        )

        assert result is not None
        assert result.roles == {"admin", "expanded"}
        auth_service.expand_roles.assert_called_once_with({"admin"})


class TestOidcDisabledPath:
    def test_oidc_disabled_returns_local_admin(self) -> None:
        auth_service = _make_auth_service()

        result = resolve_auth_identity(None, auth_service, _make_config(oidc_enabled=False))

        assert result is not None
        assert result.subject == "local-user"
        assert result.email == "admin@local"
        assert result.name == "Local Admin"
        assert result.roles == {"admin", "expanded"}
        auth_service.expand_roles.assert_called_once_with({"admin"})
        auth_service.validate_token.assert_not_called()

    def test_oidc_disabled_ignores_a_token(self) -> None:
        auth_service = _make_auth_service()

        result = resolve_auth_identity("eyJ.jwt.token", auth_service, _make_config(oidc_enabled=False))

        assert result is not None
        assert result.subject == "local-user"
        auth_service.validate_token.assert_not_called()

    def test_testing_without_token_is_local_admin(self) -> None:
        testing_service = _make_testing_service()

        result = resolve_auth_identity(
            None,
            _make_auth_service(),
            _make_config(is_testing=True, oidc_enabled=False),
            testing_service,
        )

        assert result is not None
        assert result.subject == "local-user"
        testing_service.get_session.assert_not_called()


class TestOidcEnabledPath:
    def test_valid_token(self) -> None:
        ctx = AuthContext(
            subject="oidc-sub", email="user@example.com", name="User", roles={"editor"}
        )
        auth_service = _make_auth_service(validate_token_result=ctx)

        result = resolve_auth_identity("valid-jwt", auth_service, _make_config())

        assert result is ctx
        auth_service.validate_token.assert_called_once_with("valid-jwt")

    def test_no_token_returns_none(self) -> None:
        auth_service = _make_auth_service()

        result = resolve_auth_identity(None, auth_service, _make_config())

        assert result is None
        auth_service.validate_token.assert_not_called()

    @pytest.mark.parametrize("message", ["Token has expired", "Invalid token signature"])
    def test_validation_error_propagates(self, message: str) -> None:
        auth_service = _make_auth_service(
            validate_token_side_effect=AuthenticationException(message)
        )

        with pytest.raises(AuthenticationException, match=message):
            resolve_auth_identity("bad-jwt", auth_service, _make_config())
