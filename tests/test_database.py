"""Tests for app.database's migration helpers, on PostgreSQL.

They run on committed_app, a database of their own, because they drop and
rebuild the schema.
"""

import pytest
from flask import Flask
from sqlalchemy import inspect

from app.database import (
    get_current_revision,
    get_pending_migrations,
    upgrade_database,
)
from app.extensions import db


class TestUpgradeDatabase:
    def test_a_built_database_has_nothing_pending(self, committed_app: Flask):
        with committed_app.app_context():
            assert get_pending_migrations() == []
            assert get_current_revision() is not None
            assert upgrade_database() == []

    def test_recreate_drops_and_applies_every_revision(
        self, committed_app: Flask, monkeypatch: pytest.MonkeyPatch
    ):
        # drop_all_tables() also empties db.metadata, the models' own, which
        # the CLI's one-shot process can afford and the rest of this run can't.
        monkeypatch.setattr(db.metadata, "clear", lambda: None)

        with committed_app.app_context():
            head = get_current_revision()

            applied = upgrade_database(recreate=True)

            assert [revision for revision, _ in applied] == [head[:7]]
            assert get_current_revision() == head
            assert get_pending_migrations() == []
            assert "items" in inspect(db.engine).get_table_names()
