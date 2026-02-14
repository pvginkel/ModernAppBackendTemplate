"""Item API schemas."""

from datetime import datetime

from pydantic import BaseModel, Field


class ItemCreate(BaseModel):
    """Schema for creating an item."""

    name: str = Field(..., min_length=1, max_length=200, description="Item name")
    description: str | None = Field(None, description="Item description")
    quantity: int = Field(0, ge=0, description="Item quantity")


class ItemUpdate(BaseModel):
    """Schema for updating an item."""

    name: str | None = Field(None, min_length=1, max_length=200, description="Item name")
    description: str | None = Field(None, description="Item description")
    quantity: int | None = Field(None, ge=0, description="Item quantity")


class ItemResponse(BaseModel):
    """Schema for item responses."""

    id: int
    name: str
    description: str | None
    quantity: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ItemListResponse(BaseModel):
    """Schema for item list responses."""

    items: list[ItemResponse]
    total: int
