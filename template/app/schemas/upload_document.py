"""Schema for document upload content."""

from pydantic import BaseModel


class DocumentContentSchema(BaseModel):
    """Schema representing document content with its MIME type."""

    content: bytes
    content_type: str
