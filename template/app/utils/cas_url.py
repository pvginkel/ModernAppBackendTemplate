"""CAS URL building utilities."""

import re
from urllib.parse import quote

# Compile regex for hash extraction once
CAS_KEY_PATTERN = re.compile(r'^cas/([0-9a-f]{64})$')


def build_cas_url(
    s3_key: str | None,
    content_type: str | None = None,
    filename: str | None = None,
) -> str | None:
    """Build a CAS URL from s3_key and optional metadata.

    The URL includes content_type and filename if provided.
    Disposition is NOT included - the client can add it as needed.
    Thumbnail parameter is NOT included - the client can add it for image previews.

    Args:
        s3_key: The S3 key in CAS format (cas/<hash>)
        content_type: MIME type (optional, included if provided)
        filename: Original filename (optional, included if provided)

    Returns:
        CAS URL string like /api/cas/<hash>?content_type=...&filename=...
        or None if s3_key is not in CAS format
    """
    if not s3_key:
        return None

    # Extract hash from cas/{hash} format
    match = CAS_KEY_PATTERN.match(s3_key)
    if not match:
        return None

    hash_value = match.group(1)

    # Build query params - only include what's provided
    params: list[str] = []

    if content_type:
        params.append(f"content_type={quote(content_type)}")

    if filename:
        params.append(f"filename={quote(filename)}")

    if params:
        return f"/api/cas/{hash_value}?{'&'.join(params)}"
    else:
        return f"/api/cas/{hash_value}"


def extract_cas_hash(s3_key: str | None) -> str | None:
    """Extract the hash from a CAS s3_key.

    Args:
        s3_key: The S3 key in CAS format (cas/<hash>)

    Returns:
        The 64-character hex hash, or None if not in CAS format
    """
    if not s3_key:
        return None

    match = CAS_KEY_PATTERN.match(s3_key)
    if not match:
        return None

    return match.group(1)
