"""Tests for CLI command handlers.

These tests verify that the CLI handlers (handle_upgrade_db, handle_load_test_data)
orchestrate the correct sequence of calls: database checks, migrations, and
app-specific hooks. The hooks themselves are tested in tests/test_startup.py.
"""

import pytest
from flask import Flask

import app.cli as cli


def _make_app(db_uri: str = "sqlite:///cli-test.db") -> Flask:
    """Create a minimal Flask app for CLI tests."""
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = db_uri
    return app


class TestHandleLoadTestData:
    """Tests for the load-test-data CLI handler."""

    def test_reports_target_database(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The CLI should make the target database explicit before destructive work."""
        app = _make_app()

        monkeypatch.setattr(cli, "check_db_connection", lambda: True)
        monkeypatch.setattr(cli, "upgrade_database", lambda recreate=False: [])
        monkeypatch.setattr(cli, "load_test_data_hook", lambda a: None)

        cli.handle_load_test_data(app=app, confirmed=True)

        output = capsys.readouterr().out
        assert "sqlite:///cli-test.db" in output
        assert "Using database" in output

    def test_calls_load_test_data_hook(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """After database recreation, the handler delegates to load_test_data_hook."""
        app = _make_app()
        hook_calls: list[Flask] = []

        monkeypatch.setattr(cli, "check_db_connection", lambda: True)
        monkeypatch.setattr(cli, "upgrade_database", lambda recreate=False: [])
        monkeypatch.setattr(cli, "load_test_data_hook", lambda a: hook_calls.append(a))

        cli.handle_load_test_data(app=app, confirmed=True)

        assert len(hook_calls) == 1
        assert hook_calls[0] is app

    def test_hook_failure_exits_with_code_1(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """If load_test_data_hook raises, the handler prints to stderr and exits."""
        app = _make_app()

        monkeypatch.setattr(cli, "check_db_connection", lambda: True)
        monkeypatch.setattr(cli, "upgrade_database", lambda recreate=False: [])

        def _exploding_hook(a: Flask) -> None:
            raise RuntimeError("boom")

        monkeypatch.setattr(cli, "load_test_data_hook", _exploding_hook)

        with pytest.raises(SystemExit) as exc_info:
            cli.handle_load_test_data(app=app, confirmed=True)

        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert "boom" in err

    def test_unconfirmed_exits_before_hook(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without --yes-i-am-sure, the handler exits before calling the hook."""
        app = _make_app()
        hook_calls: list[Flask] = []

        monkeypatch.setattr(cli, "check_db_connection", lambda: True)
        monkeypatch.setattr(cli, "load_test_data_hook", lambda a: hook_calls.append(a))

        with pytest.raises(SystemExit) as exc_info:
            cli.handle_load_test_data(app=app, confirmed=False)

        assert exc_info.value.code == 1
        assert len(hook_calls) == 0


class TestHandleUpgradeDb:
    """Tests for the upgrade-db CLI handler."""

    def test_calls_post_migration_hook_unconditionally(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The post-migration hook runs even when no migrations are pending."""
        app = _make_app()
        hook_calls: list[Flask] = []

        monkeypatch.setattr(cli, "check_db_connection", lambda: True)
        monkeypatch.setattr(cli, "get_current_revision", lambda: "abc1234")
        monkeypatch.setattr(cli, "get_pending_migrations", lambda: [])
        monkeypatch.setattr(cli, "post_migration_hook", lambda a: hook_calls.append(a))

        cli.handle_upgrade_db(app=app, recreate=False, confirmed=False)

        assert len(hook_calls) == 1
        assert hook_calls[0] is app

    def test_calls_post_migration_hook_after_migrations(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The post-migration hook runs after migrations are applied."""
        app = _make_app()
        call_order: list[str] = []

        monkeypatch.setattr(cli, "check_db_connection", lambda: True)
        monkeypatch.setattr(cli, "get_current_revision", lambda: "abc1234")
        monkeypatch.setattr(cli, "get_pending_migrations", lambda: ["def5678"])

        def _fake_upgrade(recreate: bool = False) -> list[tuple[str, str]]:
            call_order.append("upgrade")
            return [("def5678", "Add widgets")]

        def _fake_hook(a: Flask) -> None:
            call_order.append("hook")

        monkeypatch.setattr(cli, "upgrade_database", _fake_upgrade)
        monkeypatch.setattr(cli, "post_migration_hook", _fake_hook)

        cli.handle_upgrade_db(app=app, recreate=False, confirmed=False)

        assert call_order == ["upgrade", "hook"]

    def test_reports_target_database(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The CLI should print the target database URI."""
        app = _make_app("sqlite:///upgrade-test.db")

        monkeypatch.setattr(cli, "check_db_connection", lambda: True)
        monkeypatch.setattr(cli, "get_current_revision", lambda: None)
        monkeypatch.setattr(cli, "get_pending_migrations", lambda: [])
        monkeypatch.setattr(cli, "post_migration_hook", lambda a: None)

        cli.handle_upgrade_db(app=app)

        output = capsys.readouterr().out
        assert "sqlite:///upgrade-test.db" in output
        assert "Using database" in output

    def test_recreate_without_confirm_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--recreate without --yes-i-am-sure exits before any work."""
        app = _make_app()
        hook_calls: list[Flask] = []

        monkeypatch.setattr(cli, "check_db_connection", lambda: True)
        monkeypatch.setattr(cli, "post_migration_hook", lambda a: hook_calls.append(a))

        with pytest.raises(SystemExit) as exc_info:
            cli.handle_upgrade_db(app=app, recreate=True, confirmed=False)

        assert exc_info.value.code == 1
        assert len(hook_calls) == 0


class TestCliGroup:
    """Test the Click CLI group structure."""

    def test_cli_group_has_upgrade_db_command(self) -> None:
        """The CLI group should have an upgrade-db command."""
        assert "upgrade-db" in cli.cli.commands

    def test_cli_group_has_load_test_data_command(self) -> None:
        """The CLI group should have a load-test-data command."""
        assert "load-test-data" in cli.cli.commands
