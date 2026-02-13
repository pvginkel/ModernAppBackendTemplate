"""Pydantic schemas for testing content fixture endpoints."""

from pydantic import BaseModel, Field


class ContentImageQuerySchema(BaseModel):
    """Query parameters for deterministic testing image content."""

    text: str = Field(
        ...,
        description="Text to render on the generated PNG image",
        examples=["Playwright Test Image"],
    )


class ContentHtmlQuerySchema(BaseModel):
    """Query parameters for deterministic HTML content fixtures."""

    title: str = Field(
        ...,
        description="Title to embed in the rendered HTML fixture",
        examples=["Playwright Fixture Page"],
    )
