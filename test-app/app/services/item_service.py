"""Item service for CRUD operations."""

from sqlalchemy.orm import Session

from app.exceptions import RecordNotFoundException
from app.models.item import Item


class ItemService:
    """Service for managing items."""

    def __init__(self, db_session: Session) -> None:
        self.db_session = db_session

    def list_items(self) -> list[Item]:
        """List all items."""
        return list(self.db_session.query(Item).order_by(Item.id).all())

    def get_item(self, item_id: int) -> Item:
        """Get an item by ID."""
        item = self.db_session.get(Item, item_id)
        if item is None:
            raise RecordNotFoundException("Item", item_id)
        return item

    def create_item(self, name: str, description: str | None = None, quantity: int = 0) -> Item:
        """Create a new item."""
        item = Item(name=name, description=description, quantity=quantity)
        self.db_session.add(item)
        self.db_session.flush()
        return item

    def update_item(self, item_id: int, **kwargs: object) -> Item:
        """Update an existing item."""
        item = self.get_item(item_id)
        for key, value in kwargs.items():
            if value is not None:
                setattr(item, key, value)
        self.db_session.flush()
        return item

    def delete_item(self, item_id: int) -> None:
        """Delete an item."""
        item = self.get_item(item_id)
        self.db_session.delete(item)
        self.db_session.flush()
