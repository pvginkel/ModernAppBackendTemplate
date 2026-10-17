"""Tests for AuthService's role_hierarchy: cross-cutting implications on top
of the tier chain, with validation and cycle detection. Off by default.

Ported from DesignAssistant's test_auth_role_hierarchy.py, without its
portal-specific endpoint and OpenAPI cases.
"""

from collections.abc import Generator

import pytest
from dependency_injector import providers
from flask import Flask

from app.config import Settings
from app.exceptions import AuthorizationException
from app.services.auth_service import AuthContext, AuthService
from app.utils.auth import allow_roles, check_authorization


@pytest.fixture
def make_auth_service(test_settings: Settings):
    """Factory for an AuthService (OIDC off) with the given role config."""

    def _make(
        *,
        read_role: str | None = None,
        write_role: str | None = "editor",
        admin_role: str | None = None,
        additional_roles: list[str] | None = None,
        role_hierarchy: dict[str, list[str]] | None = None,
    ) -> AuthService:
        return AuthService(
            test_settings,
            read_role=read_role,
            write_role=write_role,
            admin_role=admin_role,
            additional_roles=additional_roles,
            role_hierarchy=role_hierarchy,
        )

    return _make


class TestExpandRolesWithHierarchy:
    """expand_roles applies role_hierarchy on top of the tier chain."""

    def test_editor_implies_portal(self, make_auth_service) -> None:
        svc = make_auth_service(
            additional_roles=["portal"], role_hierarchy={"editor": ["portal"]}
        )
        assert svc.expand_roles({"editor"}) == {"editor", "portal"}

    def test_portal_does_not_imply_editor(self, make_auth_service) -> None:
        svc = make_auth_service(
            additional_roles=["portal"], role_hierarchy={"editor": ["portal"]}
        )
        assert svc.expand_roles({"portal"}) == {"portal"}

    def test_both_roles_idempotent(self, make_auth_service) -> None:
        svc = make_auth_service(
            additional_roles=["portal"], role_hierarchy={"editor": ["portal"]}
        )
        assert svc.expand_roles({"editor", "portal"}) == {"editor", "portal"}

    def test_transitive_closure(self, make_auth_service) -> None:
        svc = make_auth_service(
            write_role=None,
            additional_roles=["a", "b", "c"],
            role_hierarchy={"a": ["b"], "b": ["c"]},
        )
        assert svc.expand_roles({"a"}) == {"a", "b", "c"}

    def test_transitive_middle_role(self, make_auth_service) -> None:
        svc = make_auth_service(
            write_role=None,
            additional_roles=["a", "b", "c"],
            role_hierarchy={"a": ["b"], "b": ["c"]},
        )
        assert svc.expand_roles({"b"}) == {"b", "c"}
        assert svc.expand_roles({"c"}) == {"c"}

    def test_no_hierarchy_preserves_existing_behaviour(self, make_auth_service) -> None:
        svc = make_auth_service(additional_roles=["portal"], role_hierarchy=None)
        assert svc.expand_roles({"editor"}) == {"editor"}
        assert svc.expand_roles({"portal"}) == {"portal"}

    def test_empty_hierarchy_preserves_existing_behaviour(self, make_auth_service) -> None:
        svc = make_auth_service(
            read_role="reader", admin_role="admin", role_hierarchy={}
        )
        assert svc.expand_roles({"admin"}) == {"admin", "editor", "reader"}
        assert svc.expand_roles({"editor"}) == {"editor", "reader"}

    def test_self_referential_entry_harmless(self, make_auth_service) -> None:
        svc = make_auth_service(
            additional_roles=["portal"], role_hierarchy={"editor": ["editor"]}
        )
        assert svc.expand_roles({"editor"}) == {"editor"}

    def test_hierarchy_merges_with_tier_chain(self, make_auth_service) -> None:
        """The tier chain and role_hierarchy are additive."""
        svc = make_auth_service(
            read_role="reader",
            additional_roles=["portal"],
            role_hierarchy={"editor": ["portal"]},
        )
        assert svc.expand_roles({"editor"}) == {"editor", "reader", "portal"}
        assert svc.expand_roles({"reader"}) == {"reader"}

    def test_role_above_in_tier_chain_inherits_the_implication(
        self, make_auth_service
    ) -> None:
        """An admin is an editor, so an admin gets what an editor implies."""
        svc = make_auth_service(
            read_role="reader",
            admin_role="admin",
            additional_roles=["portal"],
            role_hierarchy={"editor": ["portal"]},
        )
        assert svc.expand_roles({"admin"}) == {"admin", "editor", "reader", "portal"}

    def test_additional_role_can_imply_a_tier(self, make_auth_service) -> None:
        """An additional role implying a tier gets the tier's own implications."""
        svc = make_auth_service(
            read_role="reader",
            additional_roles=["pipeline"],
            role_hierarchy={"pipeline": ["editor"]},
        )
        assert svc.expand_roles({"pipeline"}) == {"pipeline", "editor", "reader"}

    def test_configured_and_hierarchy_roles_unchanged(self, make_auth_service) -> None:
        svc = make_auth_service(
            additional_roles=["portal"], role_hierarchy={"editor": ["portal"]}
        )
        assert svc.configured_roles == {"editor", "portal"}
        assert svc.hierarchy_roles == {"editor"}


class TestRoleHierarchyValidation:
    """Every role role_hierarchy names must be configured."""

    def test_unknown_source_role_raises(self, make_auth_service) -> None:
        with pytest.raises(ValueError, match="unknown roles"):
            make_auth_service(
                additional_roles=["portal"],
                role_hierarchy={"unknown_role": ["portal"]},
            )

    def test_unknown_target_role_raises(self, make_auth_service) -> None:
        with pytest.raises(ValueError, match="unknown roles"):
            make_auth_service(
                additional_roles=["portal"],
                role_hierarchy={"editor": ["unknown_role"]},
            )

    def test_all_unknown_roles_listed_in_error(self, make_auth_service) -> None:
        with pytest.raises(ValueError) as exc_info:
            make_auth_service(
                additional_roles=["portal"],
                role_hierarchy={"bad_one": ["bad_two"]},
            )
        message = str(exc_info.value)
        assert "bad_one" in message
        assert "bad_two" in message


class TestRoleHierarchyCycleDetection:
    """A cycle is rejected, whether role_hierarchy makes it alone or through
    the tier chain."""

    def test_direct_cycle_raises(self, make_auth_service) -> None:
        with pytest.raises(ValueError, match="cycle"):
            make_auth_service(
                write_role=None,
                additional_roles=["a", "b"],
                role_hierarchy={"a": ["b"], "b": ["a"]},
            )

    def test_longer_cycle_raises(self, make_auth_service) -> None:
        with pytest.raises(ValueError, match="cycle"):
            make_auth_service(
                write_role=None,
                additional_roles=["a", "b", "c"],
                role_hierarchy={"a": ["b"], "b": ["c"], "c": ["a"]},
            )

    def test_error_names_the_cycle(self, make_auth_service) -> None:
        with pytest.raises(ValueError, match="a -> b -> c -> a"):
            make_auth_service(
                write_role=None,
                additional_roles=["a", "b", "c"],
                role_hierarchy={"a": ["b"], "b": ["c"], "c": ["a"]},
            )

    def test_cycle_through_the_tier_chain_raises(self, make_auth_service) -> None:
        """reader -> editor would let every reader write."""
        with pytest.raises(ValueError, match="cycle"):
            make_auth_service(
                read_role="reader",
                role_hierarchy={"reader": ["editor"]},
            )

    def test_self_referential_is_not_a_cycle(self, make_auth_service) -> None:
        svc = make_auth_service(
            additional_roles=["portal"], role_hierarchy={"editor": ["editor"]}
        )
        assert svc.expand_roles({"editor"}) == {"editor"}

    def test_diamond_is_not_a_cycle(self, make_auth_service) -> None:
        svc = make_auth_service(
            write_role=None,
            additional_roles=["a", "b", "c", "d"],
            role_hierarchy={"a": ["b", "c"], "b": ["d"], "c": ["d"]},
        )
        assert svc.expand_roles({"a"}) == {"a", "b", "c", "d"}

    def test_restating_the_tier_chain_is_not_a_cycle(self, make_auth_service) -> None:
        svc = make_auth_service(
            read_role="reader",
            admin_role="admin",
            role_hierarchy={"admin": ["reader"]},
        )
        assert svc.expand_roles({"admin"}) == {"admin", "editor", "reader"}


class TestRoleHierarchyProperty:
    """role_hierarchy returns the input, as a copy."""

    def test_returns_raw_input(self, make_auth_service) -> None:
        svc = make_auth_service(
            additional_roles=["portal"], role_hierarchy={"editor": ["portal"]}
        )
        assert svc.role_hierarchy == {"editor": ["portal"]}

    def test_returns_empty_dict_when_none(self, make_auth_service) -> None:
        svc = make_auth_service(additional_roles=["portal"], role_hierarchy=None)
        assert svc.role_hierarchy == {}

    def test_returns_defensive_copy(self, make_auth_service) -> None:
        svc = make_auth_service(
            additional_roles=["portal"], role_hierarchy={"editor": ["portal"]}
        )
        returned = svc.role_hierarchy
        returned["hacker"] = ["admin"]
        returned["editor"].append("hacker")
        assert svc.role_hierarchy == {"editor": ["portal"]}

    def test_caller_mutating_its_input_changes_nothing(self, make_auth_service) -> None:
        hierarchy = {"editor": ["portal"]}
        svc = make_auth_service(additional_roles=["portal"], role_hierarchy=hierarchy)
        hierarchy["editor"].append("other")
        assert svc.role_hierarchy == {"editor": ["portal"]}


class TestRoleChecksWithHierarchy:
    """The role checks see the implied roles."""

    @pytest.fixture
    def svc(self, make_auth_service) -> AuthService:
        return make_auth_service(
            additional_roles=["portal"], role_hierarchy={"editor": ["portal"]}
        )

    @staticmethod
    def _caller(svc: AuthService, *roles: str) -> AuthContext:
        return AuthContext(
            subject="user", email=None, name=None, roles=svc.expand_roles(set(roles))
        )

    def test_editor_passes_a_portal_endpoint(self, svc) -> None:
        @allow_roles("portal")
        def portal_view() -> None: ...

        check_authorization(self._caller(svc, "editor"), svc, "GET", portal_view)

    def test_portal_user_is_refused_an_editor_write(self, svc) -> None:
        def write_view() -> None: ...

        with pytest.raises(AuthorizationException):
            check_authorization(self._caller(svc, "portal"), svc, "POST", write_view)


class TestOpenAPIAuthRoles:
    """x-auth-roles carries the hierarchy."""

    def test_hierarchy_is_off_by_default(self, app: Flask) -> None:
        assert app.openapi_auth_roles["hierarchy"] == {}  # type: ignore[attr-defined]


@pytest.fixture
def hierarchy_oidc_app(oidc_app: Flask) -> Generator[Flask]:
    """The OIDC app with ``editor`` writing and implying ``portal``."""
    container = oidc_app.container
    auth_service = AuthService(
        config=container.config(),
        write_role="editor",
        additional_roles=["portal"],
        role_hierarchy={"editor": ["portal"]},
    )
    container.auth_service.override(providers.Object(auth_service))
    try:
        yield oidc_app
    finally:
        container.auth_service.reset_override()


class TestAuthSelfExpandedRoles:
    """/api/auth/self returns the roles the hierarchy implies."""

    def test_editor_self_includes_portal(self, hierarchy_oidc_app, generate_test_jwt) -> None:
        client = hierarchy_oidc_app.test_client()
        client.set_cookie("access_token", generate_test_jwt(roles=["editor"]))

        response = client.get("/api/auth/self")

        assert response.status_code == 200
        assert set(response.get_json()["roles"]) == {"editor", "portal"}

    def test_portal_self_excludes_editor(self, hierarchy_oidc_app, generate_test_jwt) -> None:
        client = hierarchy_oidc_app.test_client()
        client.set_cookie("access_token", generate_test_jwt(roles=["portal"]))

        response = client.get("/api/auth/self")

        assert response.status_code == 200
        assert set(response.get_json()["roles"]) == {"portal"}
