"""The OpenAPI document lists the responses of the template's own routes.

Every error status a route answers is documented with ApiErrorResponseSchema,
the error envelope (the guide's API-5). Spectree documents only the first app
a process creates, and the suite creates many, so the document comes from an
app built in a fresh process, as tests/test_openapi_cache.py builds it.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import app as app_package

_BACKEND_DIR = Path(app_package.__file__).resolve().parent.parent

_DUMP_DOCUMENT = """
import json
import sys

from app import create_app

app = create_app(skip_background_services=True)
response = app.test_client().get("/api/docs/openapi.json")
if response.status_code != 200:
    sys.exit(f"GET /api/docs/openapi.json answered {response.status_code}")
with open(sys.argv[1], "w", encoding="utf-8") as f:
    json.dump(response.get_json(), f)
"""


@pytest.fixture(scope="module")
def document(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    document_file = tmp_path_factory.mktemp("openapi") / "openapi.json"
    result = subprocess.run(
        [sys.executable, "-c", _DUMP_DOCUMENT, str(document_file)],
        cwd=_BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr[-4000:]
    loaded: dict[str, Any] = json.loads(document_file.read_text(encoding="utf-8"))
    return loaded


def _responses(document: dict[str, Any], path: str, method: str) -> dict[str, str]:
    """Status code to the name of the schema it references, without its hash."""
    operation = document["paths"][path][method]
    names: dict[str, str] = {}
    for status, response in operation["responses"].items():
        ref = response.get("content", {}).get("application/json", {}).get("schema", {}).get("$ref", "")
        names[status] = ref.rsplit("/", 1)[-1].split(".", 1)[0]
    return names


class TestTaskRoutes:
    def test_status(self, document: dict[str, Any]):
        responses = _responses(document, "/api/tasks/{task_id}/status", "get")
        assert responses["200"] == "TaskInfo"
        assert responses["404"] == "ApiErrorResponseSchema"

    @pytest.mark.parametrize(
        ("path", "method"),
        [("/api/tasks/{task_id}/cancel", "post"), ("/api/tasks/{task_id}", "delete")],
    )
    def test_cancel_and_remove(self, document: dict[str, Any], path: str, method: str):
        responses = _responses(document, path, method)
        assert responses["200"] == "TaskActionResponse"
        assert responses["404"] == "ApiErrorResponseSchema"


class TestTestingSseRoutes:
    def test_task_event(self, document: dict[str, Any]):
        responses = _responses(document, "/api/testing/sse/task-event", "post")
        assert responses["200"] == "TaskEventResponseSchema"
        assert responses["404"] == "ApiErrorResponseSchema"
        assert responses["502"] == "ApiErrorResponseSchema"

    def test_force_callback_error(self, document: dict[str, Any]):
        responses = _responses(document, "/api/testing/sse/force-callback-error", "post")
        assert "204" in responses

    def test_no_hand_built_error_schema(self, document: dict[str, Any]):
        assert not any(
            name.startswith("TestErrorResponseSchema")
            for name in document["components"]["schemas"]
        )
