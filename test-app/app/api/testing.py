"""Testing API endpoints for Playwright test suite support."""

import logging
import time
from queue import Empty, Queue
from typing import Any

from dependency_injector.wiring import Provide, inject
from flask import Blueprint, current_app, jsonify, request
from spectree import Response as SpectreeResponse


from app.schemas.task_schema import TaskEvent, TaskEventType
from app.services.connection_manager import ConnectionManager
from app.services.version_service import VersionService

from app.schemas.testing import (
    ContentHtmlQuerySchema,
    ContentImageQuerySchema,

    DeploymentTriggerRequestSchema,
    DeploymentTriggerResponseSchema,
    TaskEventRequestSchema,
    TaskEventResponseSchema,

    TestErrorResponseSchema,
    TestResetResponseSchema,
)
from app.services.container import ServiceContainer
from app.services.task_service import TaskService
from app.services.testing_service import TestingService

from app.utils import ensure_request_id_from_query, get_current_correlation_id

from app.utils.flask_error_handlers import build_error_response

from app.utils.log_capture import LogCaptureHandler

from app.utils.spectree_config import api

from app.utils.sse_utils import create_sse_response, format_sse_event


logger = logging.getLogger(__name__)

testing_bp = Blueprint("testing", __name__, url_prefix="/api/testing")


@testing_bp.before_request
def check_testing_mode() -> Any:
    container = current_app.container
    settings = container.config()

    if not settings.is_testing:
        from app.exceptions import RouteNotAvailableException

        exception = RouteNotAvailableException()
        return build_error_response(
            exception.message,
            {"message": "Testing endpoints require FLASK_ENV=testing"},
            code=exception.error_code,
            status_code=400,
        )



@testing_bp.route("/reset", methods=["POST"])
@api.validate(resp=SpectreeResponse(HTTP_200=TestResetResponseSchema, HTTP_503=TestErrorResponseSchema))
@inject
def reset_database(
    testing_service: TestingService = Provide[ServiceContainer.testing_service]
) -> Any:
    """Reset database to clean state with optional test data seeding."""
    if testing_service.is_reset_in_progress():
        return jsonify({
            "error": "Database reset already in progress",
            "status": "busy"
        }), 503, {"Retry-After": "5"}

    seed = request.args.get("seed", "false").lower() in ("true", "1", "yes")
    result = testing_service.reset_database(seed=seed)
    return jsonify(result), 200




@testing_bp.route("/logs/stream", methods=["GET"])
def stream_logs() -> Any:
    """SSE endpoint for streaming backend application logs in real-time."""
    ensure_request_id_from_query(request.args.get("request_id"))

    def log_stream() -> Any:
        correlation_id = get_current_correlation_id()

        event_queue: Queue[tuple[str, dict[str, Any]]] = Queue()
        class QueueLogClient:
            def __init__(self, queue: Queue[tuple[str, dict[str, Any]]]):
                self.queue = queue
            def put(self, event_data: tuple[str, dict[str, Any]]) -> None:
                self.queue.put(event_data)

        client = QueueLogClient(event_queue)

        log_handler = LogCaptureHandler.get_instance()
        log_handler.register_client(client)

        shutdown_requested = False

        try:
            yield format_sse_event("connection_open", {"status": "connected"}, correlation_id)

            last_heartbeat = time.perf_counter()
            heartbeat_interval = 30.0

            while True:
                try:
                    timeout = 0.25 if shutdown_requested else 1.0
                    event_type, event_data = event_queue.get(timeout=timeout)

                    if correlation_id and "correlation_id" not in event_data:
                        event_data["correlation_id"] = correlation_id

                    yield format_sse_event(event_type, event_data)

                    if event_type == "connection_close":
                        shutdown_requested = True
                        continue

                except Empty:
                    if shutdown_requested:
                        break

                    current_time = time.perf_counter()
                    if current_time - last_heartbeat >= heartbeat_interval:
                        yield format_sse_event("heartbeat", {"timestamp": time.time()}, correlation_id)
                        last_heartbeat = current_time

        except GeneratorExit:
            shutdown_requested = True
        finally:
            log_handler.unregister_client(client)

    return create_sse_response(log_stream())



@testing_bp.route("/content/image", methods=["GET"])
@api.validate(query=ContentImageQuerySchema)
@inject
def generate_content_image(
    testing_service: TestingService = Provide[ServiceContainer.testing_service]
) -> Any:
    """Return a deterministic PNG image for Playwright fixtures."""
    query = ContentImageQuerySchema.model_validate(request.args.to_dict())
    image_bytes = testing_service.create_fake_image(query.text)

    response = current_app.response_class(image_bytes, mimetype="image/png")
    response.headers["Content-Disposition"] = "attachment; filename=testing-content-image.png"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Content-Length"] = str(len(image_bytes))
    return response


@testing_bp.route("/content/pdf", methods=["GET"])
@inject
def generate_content_pdf(
    testing_service: TestingService = Provide[ServiceContainer.testing_service]
) -> Any:
    """Return the bundled deterministic PDF asset."""
    pdf_bytes = testing_service.get_pdf_fixture()

    response = current_app.response_class(pdf_bytes, mimetype="application/pdf")
    response.headers["Content-Disposition"] = "attachment; filename=testing-content.pdf"
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Content-Length"] = str(len(pdf_bytes))
    return response


@testing_bp.route("/content/html", methods=["GET"])
@api.validate(query=ContentHtmlQuerySchema)
@inject
def generate_content_html(
    testing_service: TestingService = Provide[ServiceContainer.testing_service]
) -> Any:
    """Return deterministic HTML content."""
    query = ContentHtmlQuerySchema.model_validate(request.args.to_dict())
    html_doc = testing_service.render_html_fixture(query.title, include_banner=False)
    html_bytes = html_doc.encode("utf-8")

    response = current_app.response_class(html_bytes, mimetype="text/html; charset=utf-8")
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Content-Length"] = str(len(html_bytes))
    return response



@testing_bp.route("/deployments/version", methods=["POST"])
@api.validate(json=DeploymentTriggerRequestSchema, resp=SpectreeResponse(HTTP_202=DeploymentTriggerResponseSchema))
@inject
def trigger_version_deployment(
    version_service: VersionService = Provide[ServiceContainer.version_service]
) -> Any:
    """Trigger a deterministic version deployment notification for Playwright."""
    payload = DeploymentTriggerRequestSchema.model_validate(request.get_json() or {})
    delivered = version_service.queue_version_event(
        request_id=payload.request_id, version=payload.version, changelog=payload.changelog,
    )
    status = "delivered" if delivered else "queued"
    response_body = DeploymentTriggerResponseSchema(
        requestId=payload.request_id, delivered=delivered, status=status,
    )
    return jsonify(response_body.model_dump(by_alias=True)), 202


@testing_bp.route("/tasks/start", methods=["POST"])
@inject
def start_test_task(
    task_service: TaskService = Provide[ServiceContainer.task_service]
) -> Any:
    """Start a test task for SSE baseline testing."""
    from app.services.base_task import BaseTask

    data = request.get_json() or {}
    task_type = data.get("task_type")
    params = data.get("params", {})

    task: BaseTask
    if task_type == "demo_task":
        from tests.test_tasks.test_task import DemoTask
        task = DemoTask()
    elif task_type == "failing_task":
        from tests.test_tasks.test_task import FailingTask
        task = FailingTask()
    else:
        return jsonify({"error": f"Unknown task type: {task_type}"}), 400

    response = task_service.start_task(task, **params)
    return jsonify({"task_id": response.task_id, "status": response.status.value}), 200


_EVENT_TYPE_MAP = {
    "task_started": TaskEventType.TASK_STARTED,
    "progress_update": TaskEventType.PROGRESS_UPDATE,
    "task_completed": TaskEventType.TASK_COMPLETED,
    "task_failed": TaskEventType.TASK_FAILED,
}


@testing_bp.route("/sse/task-event", methods=["POST"])
@api.validate(json=TaskEventRequestSchema, resp=SpectreeResponse(HTTP_200=TaskEventResponseSchema, HTTP_400=TestErrorResponseSchema))
@inject
def send_task_event(
    connection_manager: ConnectionManager = Provide[ServiceContainer.connection_manager]
) -> Any:
    """Send a fake task event to a specific SSE connection for Playwright testing."""
    payload = TaskEventRequestSchema.model_validate(request.get_json() or {})

    if not connection_manager.has_connection(payload.request_id):
        return jsonify({
            "error": f"No SSE connection registered for request_id: {payload.request_id}",
            "status": "not_found"
        }), 400

    event = TaskEvent(
        event_type=_EVENT_TYPE_MAP[payload.event_type],
        task_id=payload.task_id,
        data=payload.data
    )

    success = connection_manager.send_event(
        payload.request_id, event.model_dump(mode='json'),
        event_name="task_event", service_type="task"
    )

    if not success:
        return jsonify({
            "error": f"Failed to send event to connection: {payload.request_id}",
            "status": "send_failed"
        }), 400

    response_body = TaskEventResponseSchema(
        requestId=payload.request_id, taskId=payload.task_id,
        eventType=payload.event_type, delivered=True
    )
    return jsonify(response_body.model_dump(by_alias=True)), 200

