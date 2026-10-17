"""Tests for scoped_session and scoped_savepoint, and BaseSessionTask on them.

They run on committed_app, whose commits are real, so a second connection
shows what was made durable.
"""

from typing import Any

import pytest
from flask import Flask
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import scoped_savepoint, scoped_session
from app.extensions import db
from app.services.base_task import BaseSessionTask, ProgressHandle
from app.utils.after_commit import after_commit


def _item_names(app: Flask) -> list[str]:
    """The item names, read on a connection of their own."""
    with app.app_context(), db.engine.connect() as connection:
        return list(connection.execute(text("SELECT name FROM items ORDER BY name")).scalars())


def _insert_item(session: Session, name: str) -> None:
    session.execute(text("INSERT INTO items (name, quantity) VALUES (:name, 0)"), {"name": name})


class TestScopedSession:
    def test_clean_exit_commits_then_runs_callbacks(self, committed_app: Flask):
        container = committed_app.container
        calls: list[list[str]] = []

        with committed_app.app_context():
            with scoped_session(container) as session:
                _insert_item(session, "kept")
                after_commit(lambda: calls.append(_item_names(committed_app)))

        assert _item_names(committed_app) == ["kept"]
        # The callback ran after the commit: it saw the row.
        assert calls == [["kept"]]

    def test_exception_rolls_back_and_drops_callbacks(self, committed_app: Flask):
        container = committed_app.container
        calls: list[str] = []

        with committed_app.app_context():
            with pytest.raises(RuntimeError, match="boom"), scoped_session(container) as session:
                _insert_item(session, "lost")
                after_commit(lambda: calls.append("ran"))
                raise RuntimeError("boom")

            # Nothing leaks into the next block in this thread either.
            with scoped_session(container):
                pass

        assert _item_names(committed_app) == []
        assert calls == []

    def test_session_is_closed_and_provider_reset(self, committed_app: Flask):
        container = committed_app.container

        with committed_app.app_context():
            with scoped_session(container) as session:
                _insert_item(session, "x")
                assert session.in_transaction()

            assert not session.in_transaction()
            assert container.db_session() is not session
            container.db_session().close()
            container.db_session.reset()


class TestScopedSavepoint:
    def test_failed_savepoint_keeps_the_outer_transaction(self, committed_app: Flask):
        container = committed_app.container

        with committed_app.app_context(), scoped_session(container) as session:
            for name in ("first", "bad", "third"):
                try:
                    with scoped_savepoint(session):
                        _insert_item(session, name)
                        if name == "bad":
                            raise ValueError(name)
                except ValueError:
                    pass

        assert _item_names(committed_app) == ["first", "third"]


class _Result(BaseModel):
    count: int


class _InsertTask(BaseSessionTask):
    def __init__(self, container: Any, fail: bool = False) -> None:
        super().__init__(container)
        self.fail = fail
        self.callback_calls = 0

    def execute_session(
        self, session: Session, progress_handle: ProgressHandle, **kwargs: Any
    ) -> BaseModel:
        _insert_item(session, "from-task")
        after_commit(self._on_commit)
        if self.fail:
            raise RuntimeError("task failed")
        return _Result(count=1)

    def _on_commit(self) -> None:
        self.callback_calls += 1


class TestBaseSessionTask:
    def test_success_commits_and_runs_callbacks(self, committed_app: Flask):
        task = _InsertTask(committed_app.container)

        with committed_app.app_context():
            result = task.execute(progress_handle=None)  # type: ignore[arg-type]

        assert result == _Result(count=1)
        assert _item_names(committed_app) == ["from-task"]
        assert task.callback_calls == 1

    def test_failure_rolls_back_without_callbacks(self, committed_app: Flask):
        task = _InsertTask(committed_app.container, fail=True)

        with committed_app.app_context(), pytest.raises(RuntimeError, match="task failed"):
            task.execute(progress_handle=None)  # type: ignore[arg-type]

        assert _item_names(committed_app) == []
        assert task.callback_calls == 0
