"""Items API blueprint."""

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, Response, jsonify, request
from spectree import Response as SpectreeResponse

from app.schemas.item_schema import ItemCreate, ItemResponse, ItemUpdate
from app.services.container import ServiceContainer
from app.services.item_service import ItemService
from app.utils.spectree_config import api

items_bp = Blueprint("items", __name__, url_prefix="/items")


@items_bp.route("", methods=["GET"])
@inject
def list_items(
    item_service: ItemService = Provide[ServiceContainer.item_service],
) -> Response:
    """List all items."""
    items = item_service.list_items()
    return jsonify({
        "items": [
            {
                "id": item.id,
                "name": item.name,
                "description": item.description,
                "quantity": item.quantity,
                "created_at": item.created_at.isoformat() if item.created_at else None,
                "updated_at": item.updated_at.isoformat() if item.updated_at else None,
            }
            for item in items
        ],
        "total": len(items),
    })


@items_bp.route("", methods=["POST"])
@api.validate(json=ItemCreate, resp=SpectreeResponse(HTTP_201=ItemResponse))
@inject
def create_item(
    item_service: ItemService = Provide[ServiceContainer.item_service],
) -> tuple[Response, int]:
    """Create a new item."""
    data = ItemCreate.model_validate(request.get_json())
    item = item_service.create_item(
        name=data.name,
        description=data.description,
        quantity=data.quantity,
    )
    return jsonify({
        "id": item.id,
        "name": item.name,
        "description": item.description,
        "quantity": item.quantity,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }), 201


@items_bp.route("/<int:item_id>", methods=["GET"])
@inject
def get_item(
    item_id: int,
    item_service: ItemService = Provide[ServiceContainer.item_service],
) -> Response:
    """Get an item by ID."""
    item = item_service.get_item(item_id)
    return jsonify({
        "id": item.id,
        "name": item.name,
        "description": item.description,
        "quantity": item.quantity,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    })


@items_bp.route("/<int:item_id>", methods=["PATCH"])
@api.validate(json=ItemUpdate, resp=SpectreeResponse(HTTP_200=ItemResponse))
@inject
def update_item(
    item_id: int,
    item_service: ItemService = Provide[ServiceContainer.item_service],
) -> Response:
    """Update an item."""
    data = ItemUpdate.model_validate(request.get_json())
    update_fields = data.model_dump(exclude_unset=True)
    item = item_service.update_item(item_id, **update_fields)
    return jsonify({
        "id": item.id,
        "name": item.name,
        "description": item.description,
        "quantity": item.quantity,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    })


@items_bp.route("/<int:item_id>", methods=["DELETE"])
@inject
def delete_item(
    item_id: int,
    item_service: ItemService = Provide[ServiceContainer.item_service],
) -> tuple[Response, int]:
    """Delete an item."""
    item_service.delete_item(item_id)
    return jsonify({"message": "Item deleted"}), 204
