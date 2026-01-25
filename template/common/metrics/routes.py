"""Metrics API endpoint for Prometheus scraping."""

from typing import Any

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, Response

from common.metrics.service import MetricsService

metrics_bp = Blueprint("metrics", __name__, url_prefix="/metrics")


@metrics_bp.route("", methods=["GET"])
@inject
def get_metrics(
    metrics_service: MetricsService = Provide["metrics_service"],
) -> Any:
    """Return metrics in Prometheus text format.

    Returns:
        Response with metrics data in Prometheus exposition format
    """
    metrics_text = metrics_service.get_metrics_text()

    return Response(
        metrics_text,
        content_type="text/plain; version=0.0.4; charset=utf-8",
    )
