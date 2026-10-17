"""Tests for apply_sorting(), build_pagination_meta() and the pagination schemas."""

from typing import Any, Literal

import pytest
from pydantic import ValidationError
from sqlalchemy import Select, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.models import Item
from app.schemas.common import PaginationMetaSchema, PaginationQuerySchema
from app.utils.pagination import apply_sorting, build_pagination_meta


def _order_by(query: Select[Any]) -> str:
    sql = str(query.compile(dialect=postgresql.dialect()))
    return sql.split("ORDER BY", 1)[1].strip()


class TestApplySorting:
    @pytest.mark.parametrize("direction", ["asc", "desc"])
    def test_id_breaks_ties_in_the_same_direction(self, direction: Literal["asc", "desc"]):
        query = apply_sorting(select(Item), Item, "quantity", direction)

        assert _order_by(query) == (
            f"items.quantity {direction.upper()}, items.id {direction.upper()}"
        )

    def test_sorting_by_id_needs_no_tiebreaker(self):
        query = apply_sorting(select(Item), Item, "id", "desc")

        assert _order_by(query) == "items.id DESC"

    def test_unknown_column_is_refused(self):
        with pytest.raises(ValueError, match="no column 'nope'"):
            apply_sorting(select(Item), Item, "nope", "asc")

    def test_unknown_direction_is_refused(self):
        with pytest.raises(ValueError, match="sort_dir"):
            apply_sorting(select(Item), Item, "quantity", "sideways")  # type: ignore[arg-type]

    @pytest.mark.parametrize("direction", ["asc", "desc"])
    def test_pages_over_duplicates_hold_every_row_once(
        self, session: Session, direction: Literal["asc", "desc"]
    ):
        """The bug the tiebreaker fixes: tied rows repeating or vanishing between pages."""
        items = [Item(name=f"item-{n}", quantity=n % 2) for n in range(9)]
        session.add_all(items)
        session.flush()

        seen: list[int] = []
        for page in range(1, 6):
            params = PaginationQuerySchema(page=page, per_page=2, sort_dir=direction)
            query = apply_sorting(select(Item.id), Item, "quantity", params.sort_dir)
            seen.extend(session.scalars(query.offset(params.offset).limit(params.per_page)))

        reverse = direction == "desc"
        expected = [
            item.id
            for item in sorted(items, key=lambda i: (i.quantity, i.id), reverse=reverse)
        ]
        assert seen == expected


class TestBuildPaginationMeta:
    def test_counts_pages(self):
        meta = build_pagination_meta(41, page=3, per_page=20, sort_by="name", sort_dir="desc")

        assert meta == PaginationMetaSchema(
            page=3, per_page=20, total=41, total_pages=3, sort_by="name", sort_dir="desc"
        )

    def test_an_empty_list_has_one_page(self):
        assert build_pagination_meta(0, page=1, per_page=20).total_pages == 1


class TestPaginationSchemas:
    def test_meta_without_sort_fields_still_validates(self):
        """Apps that build the meta schema today pass no sort fields."""
        meta = PaginationMetaSchema(page=1, per_page=20, total=5, total_pages=1)

        assert (meta.sort_by, meta.sort_dir) == (None, None)

    def test_query_defaults_and_offset(self):
        params = PaginationQuerySchema()

        assert (params.page, params.per_page, params.sort_dir, params.offset) == (1, 20, "asc", 0)
        assert PaginationQuerySchema(page=3, per_page=25).offset == 50

    @pytest.mark.parametrize(
        "query", [{"page": 0}, {"per_page": 0}, {"per_page": 101}, {"sort_dir": "up"}]
    )
    def test_query_bounds(self, query: dict[str, Any]):
        with pytest.raises(ValidationError):
            PaginationQuerySchema(**query)

    def test_subclass_whitelists_sort_by(self):
        class ItemListQuery(PaginationQuerySchema):
            sort_by: Literal["name", "created_at"] = "created_at"

        assert ItemListQuery(sort_by="name").sort_by == "name"
        with pytest.raises(ValidationError):
            ItemListQuery(sort_by="description")
