"""Item service for test-app."""

import logging

from sqlalchemy.orm import Session

from app.exceptions import RecordNotFoundException
from app.models.item import Item

logger = logging.getLogger(__name__)


class ItemService:
    """Service for managing items."""

    def __init__(self, db: Session):
        self.db = db

    def get_all(self) -> list[Item]:
        """Get all items."""
        return self.db.query(Item).order_by(Item.id).all()

    def get_by_id(self, item_id: int) -> Item:
        """Get item by ID."""
        item = self.db.query(Item).filter(Item.id == item_id).first()
        if not item:
            raise RecordNotFoundException("Item", item_id)
        return item

    def create(self, name: str, description: str | None = None, quantity: int = 0) -> Item:
        """Create a new item."""
        item = Item(name=name, description=description, quantity=quantity)
        self.db.add(item)
        self.db.flush()
        return item

    def update(self, item_id: int, **kwargs) -> Item:
        """Update an item."""
        item = self.get_by_id(item_id)
        for key, value in kwargs.items():
            if value is not None:
                setattr(item, key, value)
        self.db.flush()
        return item

    def delete(self, item_id: int) -> None:
        """Delete an item."""
        item = self.get_by_id(item_id)
        self.db.delete(item)
        self.db.flush()
