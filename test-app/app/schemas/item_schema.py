"""Item schemas for test-app."""

from datetime import datetime

from pydantic import BaseModel, Field


class ItemCreateSchema(BaseModel):
    """Schema for creating an item."""

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    quantity: int = Field(default=0, ge=0)


class ItemUpdateSchema(BaseModel):
    """Schema for updating an item."""

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    quantity: int | None = Field(None, ge=0)


class ItemResponseSchema(BaseModel):
    """Schema for item responses."""

    id: int
    name: str
    description: str | None
    quantity: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ItemListResponseSchema(BaseModel):
    """Schema for item list responses."""

    items: list[ItemResponseSchema]
    total: int
