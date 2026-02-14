import magic


def detect_mime_type(content: bytes, http_content_type: str | None = None) -> str:
    """
    Detect MIME type, trusting HTTP Content-Type header for common web content.

    The HTTP Content-Type header is authoritative - if the server says it's serving
    HTML, PDF, or an image, we trust that. Otherwise we fall back to magic detection.

    Args:
        content: Downloaded content bytes or string
        http_content_type: Optional Content-Type header from HTTP response
    Returns:
        Detected MIME type string
    """

    # If server provides a Content-Type header for common web content, trust it
    if http_content_type:
        # Extract just the MIME type (strip charset and other parameters)
        header_mime = http_content_type.split(';')[0].strip().lower()

        # Trust the server for HTML, PDF, and images
        if header_mime == 'text/html' or header_mime == 'application/pdf' or header_mime.startswith('image/'):
            return header_mime

    # Fall back to magic detection for everything else
    # Strip leading whitespace for better detection (some sites add blank lines before <!DOCTYPE>)
    stripped_content = content.lstrip()
    return magic.from_buffer(stripped_content, mime=True)
