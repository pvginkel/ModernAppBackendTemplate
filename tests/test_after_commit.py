"""Tests for after_commit callbacks run by the request teardown."""

from flask import Flask, g

from app.utils.after_commit import (
    after_commit,
    clear_after_commit_callbacks,
    run_after_commit_callbacks,
)


class TestAfterCommitUtility:
    """Registration, coalescing and failure isolation."""

    def test_same_callback_registered_twice_runs_once(self):
        calls: list[str] = []

        def callback() -> None:
            calls.append("ran")

        after_commit(callback)
        after_commit(callback)
        run_after_commit_callbacks()
        clear_after_commit_callbacks()

        assert calls == ["ran"]

    def test_failing_callback_does_not_stop_the_others(self):
        calls: list[str] = []

        def failing() -> None:
            raise RuntimeError("boom")

        def succeeding() -> None:
            calls.append("ran")

        after_commit(failing)
        after_commit(succeeding)
        run_after_commit_callbacks()
        clear_after_commit_callbacks()

        assert calls == ["ran"]

    def test_clear_drops_pending_callbacks(self):
        calls: list[str] = []
        after_commit(lambda: calls.append("ran"))
        clear_after_commit_callbacks()
        run_after_commit_callbacks()

        assert calls == []


class TestAfterCommitTeardown:
    """The request teardown runs callbacks on commit and drops them on rollback."""

    def test_callback_runs_after_successful_request(self, app: Flask):
        calls: list[str] = []

        def view() -> str:
            after_commit(lambda: calls.append("committed"))
            return "ok"

        app.add_url_rule("/test-after-commit", "test_after_commit", view)
        response = app.test_client().get("/test-after-commit")

        assert response.status_code == 200
        assert calls == ["committed"]

    def test_callback_dropped_when_request_rolls_back(self, app: Flask):
        calls: list[str] = []

        def view() -> str:
            after_commit(lambda: calls.append("committed"))
            g.needs_rollback = True
            return "ok"

        app.add_url_rule("/test-after-rollback", "test_after_rollback", view)
        app.add_url_rule("/test-after-noop", "test_after_noop", lambda: "ok")
        client = app.test_client()
        client.get("/test-after-rollback")

        assert calls == []

        # Nothing leaks into the next request either.
        client.get("/test-after-noop")
        assert calls == []
