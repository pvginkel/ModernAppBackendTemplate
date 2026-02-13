"""Testing content fixture endpoints for Playwright test suite support."""

from typing import Any

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, current_app, request

from app.schemas.testing_content import ContentHtmlQuerySchema, ContentImageQuerySchema
from app.services.container import ServiceContainer
from app.services.testing_service import TestingService
from app.utils.spectree_config import api

testing_content_bp = Blueprint("testing_content", __name__, url_prefix="/api/testing/content")


@testing_content_bp.before_request
def check_testing_mode() -> Any:
    """Reject requests when the server is not running in testing mode."""
    from app.api.testing_guard import reject_if_not_testing

    return reject_if_not_testing()


@testing_content_bp.route("/image", methods=["GET"])
@api.validate(query=ContentImageQuerySchema)
@inject
def generate_content_image(
    testing_service: TestingService = Provide[ServiceContainer.testing_service],
) -> Any:
    """Return a deterministic PNG image for Playwright fixtures."""
    query = ContentImageQuerySchema.model_validate(request.args.to_dict())
    image_bytes = testing_service.create_fake_image(query.text)

    response = current_app.response_class(image_bytes, mimetype="image/png")
    response.headers["Content-Disposition"] = "attachment; filename=testing-content-image.png"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Content-Length"] = str(len(image_bytes))
    return response


@testing_content_bp.route("/pdf", methods=["GET"])
@inject
def generate_content_pdf(
    testing_service: TestingService = Provide[ServiceContainer.testing_service],
) -> Any:
    """Return the bundled deterministic PDF asset."""
    pdf_bytes = testing_service.get_pdf_fixture()

    response = current_app.response_class(pdf_bytes, mimetype="application/pdf")
    response.headers["Content-Disposition"] = "attachment; filename=testing-content.pdf"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Content-Length"] = str(len(pdf_bytes))
    return response


@testing_content_bp.route("/html", methods=["GET"])
@api.validate(query=ContentHtmlQuerySchema)
@inject
def generate_content_html(
    testing_service: TestingService = Provide[ServiceContainer.testing_service],
) -> Any:
    """Return deterministic HTML content without deployment banner."""
    query = ContentHtmlQuerySchema.model_validate(request.args.to_dict())
    html_doc = testing_service.render_html_fixture(query.title, include_banner=False)
    html_bytes = html_doc.encode("utf-8")

    response = current_app.response_class(html_bytes, mimetype="text/html; charset=utf-8")
    response.headers["Content-Disposition"] = "inline; filename=testing-content.html"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Content-Length"] = str(len(html_bytes))
    return response


@testing_content_bp.route("/html-with-banner", methods=["GET"])
@api.validate(query=ContentHtmlQuerySchema)
@inject
def generate_content_html_with_banner(
    testing_service: TestingService = Provide[ServiceContainer.testing_service],
) -> Any:
    """Return deterministic HTML content that includes a deployment banner wrapper."""
    query = ContentHtmlQuerySchema.model_validate(request.args.to_dict())
    html_doc = testing_service.render_html_fixture(query.title, include_banner=True)
    html_bytes = html_doc.encode("utf-8")

    response = current_app.response_class(html_bytes, mimetype="text/html; charset=utf-8")
    response.headers["Content-Disposition"] = "inline; filename=testing-content-banner.html"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Content-Length"] = str(len(html_bytes))
    return response
