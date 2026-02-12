"""Items API endpoints for test-app."""

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, request
from spectree import Response as SpectreeResponse

from app.schemas.item_schema import (
    ItemCreateSchema,
    ItemListResponseSchema,
    ItemResponseSchema,
    ItemUpdateSchema,
)
from app.services.container import ServiceContainer
from app.services.item_service import ItemService
from app.utils.spectree_config import api

items_bp = Blueprint("items", __name__, url_prefix="/items")


@items_bp.route("", methods=["GET"])
@api.validate(resp=SpectreeResponse(HTTP_200=ItemListResponseSchema))
@inject
def list_items(
    item_service: ItemService = Provide[ServiceContainer.item_service],
):
    """List all items."""
    items = item_service.get_all()
    return ItemListResponseSchema(
        items=[ItemResponseSchema.model_validate(item) for item in items],
        total=len(items),
    ).model_dump()


@items_bp.route("", methods=["POST"])
@api.validate(resp=SpectreeResponse(HTTP_201=ItemResponseSchema), json=ItemCreateSchema)
@inject
def create_item(
    item_service: ItemService = Provide[ServiceContainer.item_service],
):
    """Create a new item."""
    data = ItemCreateSchema(**request.get_json())
    item = item_service.create(
        name=data.name,
        description=data.description,
        quantity=data.quantity,
    )
    return ItemResponseSchema.model_validate(item).model_dump(), 201


@items_bp.route("/<int:item_id>", methods=["GET"])
@api.validate(resp=SpectreeResponse(HTTP_200=ItemResponseSchema))
@inject
def get_item(
    item_id: int,
    item_service: ItemService = Provide[ServiceContainer.item_service],
):
    """Get an item by ID."""
    item = item_service.get_by_id(item_id)
    return ItemResponseSchema.model_validate(item).model_dump()


@items_bp.route("/<int:item_id>", methods=["PUT"])
@api.validate(resp=SpectreeResponse(HTTP_200=ItemResponseSchema), json=ItemUpdateSchema)
@inject
def update_item(
    item_id: int,
    item_service: ItemService = Provide[ServiceContainer.item_service],
):
    """Update an item."""
    data = ItemUpdateSchema(**request.get_json())
    item = item_service.update(item_id, **data.model_dump(exclude_unset=True))
    return ItemResponseSchema.model_validate(item).model_dump()


@items_bp.route("/<int:item_id>", methods=["DELETE"])
@inject
def delete_item(
    item_id: int,
    item_service: ItemService = Provide[ServiceContainer.item_service],
):
    """Delete an item."""
    item_service.delete(item_id)
    return "", 204
