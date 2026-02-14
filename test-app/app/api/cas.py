"""Content-Addressable Storage (CAS) API endpoint for immutable blob serving."""

import logging
import re
from typing import Any

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, request, send_file
from werkzeug.exceptions import BadRequest, InternalServerError, NotFound

from app.services.cas_image_service import CasImageService
from app.services.container import ServiceContainer
from app.services.s3_service import S3Service

cas_bp = Blueprint("cas", __name__, url_prefix="/api/cas")

logger = logging.getLogger(__name__)

# Compile regex for hash validation once
HASH_PATTERN = re.compile(r'^[0-9a-f]{64}$')


@cas_bp.route("/<hash_value>", methods=["GET"])
@inject
def get_cas_content(
    hash_value: str,
    s3_service: S3Service = Provide[ServiceContainer.s3_service],
    cas_image_service: CasImageService = Provide[ServiceContainer.cas_image_service]
) -> Any:
    """Serve content from CAS storage with immutable caching.

    This endpoint is stateless - no database access. All metadata comes from URL params.

    Query params:
        content_type: MIME type (optional, defaults to application/octet-stream)
        disposition: inline|attachment (optional, defaults to inline if filename set)
        filename: filename for Content-Disposition header (optional)
        thumbnail: pixel size for square thumbnail (mutually exclusive with content_type)

    Returns:
        Binary content with Cache-Control: immutable header
    """
    # Validate hash format
    if not HASH_PATTERN.match(hash_value):
        raise BadRequest("Invalid hash format (expected 64-char hex)")

    # Parse query params
    content_type = request.args.get('content_type')
    disposition = request.args.get('disposition')
    filename = request.args.get('filename')
    thumbnail_size_str = request.args.get('thumbnail')

    # Validate parameter combinations
    if content_type and thumbnail_size_str:
        raise BadRequest("Cannot specify both content_type and thumbnail parameters")

    # Handle thumbnail request
    if thumbnail_size_str:
        try:
            thumbnail_size = int(thumbnail_size_str)
        except ValueError:
            raise BadRequest("Invalid thumbnail size (expected integer)") from None

        # Validate thumbnail size range (prevent excessive memory/CPU usage)
        if thumbnail_size < 1 or thumbnail_size > 1000:
            raise BadRequest("Thumbnail size must be between 1 and 1000 pixels")

        # Generate or retrieve cached thumbnail
        try:
            thumbnail_path = cas_image_service.get_thumbnail_for_hash(hash_value, thumbnail_size)
        except Exception as e:
            logger.error(f"Failed to generate thumbnail for hash {hash_value}: {str(e)}")
            raise InternalServerError("Failed to generate thumbnail") from e

        # Serve thumbnail with immutable cache headers
        response = send_file(
            thumbnail_path,
            mimetype='image/jpeg',
            as_attachment=False
        )
        response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        return response

    # Handle direct content request
    s3_key = f"cas/{hash_value}"

    try:
        file_data = s3_service.download_file(s3_key)
    except Exception as e:
        logger.warning(f"Content not found for hash {hash_value}: {str(e)}")
        raise NotFound("Content not found") from e

    # Serve content with immutable cache headers
    # Default to application/octet-stream if content_type not specified
    response = send_file(
        file_data,
        mimetype=content_type or 'application/octet-stream',
        as_attachment=(disposition == 'attachment')
    )
    response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'

    # Build Content-Disposition header only if filename or disposition explicitly set
    if filename and disposition:
        response.headers['Content-Disposition'] = f'{disposition}; filename="{filename}"'
    elif filename:
        # Default to inline if filename set but disposition not
        response.headers['Content-Disposition'] = f'inline; filename="{filename}"'
    elif disposition:
        response.headers['Content-Disposition'] = disposition

    return response
