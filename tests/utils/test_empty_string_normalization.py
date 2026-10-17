"""Tests for the empty-string-to-NULL normalization and its allow_empty opt-out."""

from sqlalchemy import Integer, String, Text, inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.models import Item
from app.utils.empty_string_normalization import normalize_empty_strings


class _Base(DeclarativeBase):
    """A base of its own, so the test model stays out of db.metadata."""


class _Note(_Base):
    __tablename__ = "normalization_test_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str | None] = mapped_column(String(50))
    body: Mapped[str | None] = mapped_column(Text, info={"allow_empty": True})


class TestNormalizeEmptyStrings:
    def test_empty_and_blank_strings_become_none(self):
        note = _Note(title="   ", body="kept")

        normalize_empty_strings(inspect(_Note), None, note)

        assert note.title is None
        assert note.body == "kept"

    def test_allow_empty_column_keeps_its_empty_string(self):
        note = _Note(title="", body="")

        normalize_empty_strings(inspect(_Note), None, note)

        assert note.title is None
        assert note.body == ""

    def test_listener_runs_on_insert(self, session: Session):
        item = Item(name="normalized", description="")
        session.add(item)
        session.flush()

        assert item.description is None
