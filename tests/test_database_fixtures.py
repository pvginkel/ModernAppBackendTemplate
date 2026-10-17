"""Tests for the suite's PostgreSQL fixtures (tests/conftest_infrastructure.py).

They pin what every generated app's suite relies on: each test's writes are
rolled back, commit() and rollback() on the test's session are real, requests
share the test's session, and the second tier's commits are real.
"""

from typing import Any

from flask import Flask
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.extensions import db
from app.services.container import ServiceContainer
from tests.conftest_infrastructure import (
    _DATABASE_PREFIX,
    RunDatabases,
    _admin_url,
    _database_url,
)

# A table no revision creates, made by the tests themselves.
_PROBE = "fixture_probe"


def _table_exists(session_or_connection: Any, table: str = _PROBE) -> bool:
    return session_or_connection.execute(
        text("SELECT to_regclass(:table) IS NOT NULL"), {"table": table}
    ).scalar()


def _probe_values(session: Session) -> list[str]:
    return list(session.execute(text(f"SELECT value FROM {_PROBE} ORDER BY value")).scalars())


class TestRollbackTier:
    """The default tier: one transaction per test, savepoints inside it."""

    def test_writes_stay_inside_the_test(self, app: Flask, session: Session):
        session.execute(text(f"CREATE TABLE {_PROBE} (value text)"))
        session.execute(text(f"INSERT INTO {_PROBE} VALUES ('a')"))
        session.commit()

        assert _probe_values(session) == ["a"]

        # Another connection sees nothing: the commit released a savepoint.
        with app.app_context():
            engine = db.engine
        with engine.connect() as other:
            assert not _table_exists(other)

    def test_the_previous_test_left_nothing(self, session: Session):
        assert not _table_exists(session)

    def test_commit_fires_the_after_commit_event(self, session: Session):
        commits: list[Session] = []
        event.listen(session, "after_commit", commits.append)

        session.execute(text("SELECT 1"))
        session.commit()

        assert commits == [session]

    def test_rollback_returns_to_the_last_savepoint(self, session: Session):
        session.execute(text(f"CREATE TABLE {_PROBE} (value text)"))
        session.execute(text(f"INSERT INTO {_PROBE} VALUES ('kept')"))
        session.commit()

        session.execute(text(f"INSERT INTO {_PROBE} VALUES ('dropped')"))
        session.rollback()

        assert _probe_values(session) == ["kept"]

    def test_close_keeps_the_session_usable(self, session: Session):
        session.execute(text(f"CREATE TABLE {_PROBE} (value text)"))
        session.close()

        assert _table_exists(session)

    def test_container_and_requests_share_the_test_session(
        self, container: ServiceContainer, session: Session, client: Any
    ):
        assert container.db_session() is session

        # Request teardown commits and closes the request's session, which is
        # this one: it stays usable, and still in the test's transaction.
        session.execute(text(f"CREATE TABLE {_PROBE} (value text)"))
        response = client.get("/health/healthz")

        assert response.status_code == 200
        assert _table_exists(session)


class TestCommittedTier:
    """committed_app: a database of its own, where commits are real."""

    def test_commits_are_seen_by_another_connection(self, committed_app: Flask):
        with committed_app.app_context():
            url = db.engine.url
            db.session.execute(text(f"CREATE TABLE {_PROBE} (value text)"))
            db.session.execute(text(f"INSERT INTO {_PROBE} VALUES ('a')"))
            db.session.commit()
            db.session.remove()

        other_engine = create_engine(url, poolclass=NullPool)
        try:
            with other_engine.connect() as other:
                values = list(other.execute(text(f"SELECT value FROM {_PROBE}")).scalars())
        finally:
            other_engine.dispose()

        assert values == ["a"]

    def test_runs_on_its_own_database(self, app: Flask, committed_app: Flask):
        assert (
            committed_app.config["SQLALCHEMY_DATABASE_URI"]
            != app.config["SQLALCHEMY_DATABASE_URI"]
        )

    def test_is_built_by_the_revisions(self, committed_app: Flask):
        with committed_app.app_context():
            assert _table_exists(db.session, "alembic_version")
            db.session.remove()


class TestSweep:
    """At start, a run drops what dead runs left, and nothing else."""

    def test_sweep_drops_only_what_dead_runs_left(self, run_databases: RunDatabases):
        admin = create_engine(_admin_url(), isolation_level="AUTOCOMMIT", poolclass=NullPool)
        live_run = RunDatabases()
        stale = f"{_DATABASE_PREFIX}00000000_tpl"
        in_use = f"{_DATABASE_PREFIX}11111111_tpl"
        live = f"{live_run.prefix}_tpl"
        try:
            with admin.connect() as conn:
                for name in (stale, in_use, live):
                    conn.execute(text(f'CREATE DATABASE "{name}"'))

            holder = create_engine(_database_url(in_use), poolclass=NullPool)
            with holder.connect():
                run_databases.sweep()
            holder.dispose()

            with admin.connect() as conn:
                remaining = set(
                    conn.execute(
                        text("SELECT datname FROM pg_database WHERE starts_with(datname, :p)"),
                        {"p": _DATABASE_PREFIX},
                    ).scalars()
                )
            # The run's own databases are live too.
            assert {in_use, live, run_databases.template} <= remaining
            assert stale not in remaining
        finally:
            live_run.close()
            with admin.connect() as conn:
                for name in (stale, in_use):
                    conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            admin.dispose()
