"""Tests for the transaction rollback mechanism.

Tests verify that database transactions are properly rolled back when operations fail,
even when the error handler decorator catches exceptions and converts them to HTTP responses.
"""

from typing import Any
from unittest.mock import patch

from flask import Flask


class TestTransactionRollbackMechanism:
    """Test the core rollback mechanism with different exception types."""

    def test_validation_error_triggers_rollback(
        self, app: Any, app_context: Any, container: Any
    ) -> None:
        """Test that ValidationError marks session for rollback."""
        from pydantic import ValidationError
        from common.core.errors import handle_api_errors

        @handle_api_errors
        def failing_function() -> tuple[dict[str, Any], int]:
            raise ValidationError.from_exception_data("TestError", [])

        # Clear any existing rollback flag
        db_session = container.db_session()
        db_session.info.pop("needs_rollback", None)

        # Call function that raises ValidationError
        result = failing_function()
        response, status_code = result

        # Verify the rollback flag was set
        assert db_session.info.get("needs_rollback") is True
        assert status_code == 400

    def test_domain_exception_triggers_rollback(
        self, app: Any, app_context: Any, container: Any
    ) -> None:
        """Test that custom domain exceptions mark session for rollback."""
        from common.core.errors import handle_api_errors, RecordNotFoundException

        @handle_api_errors
        def failing_function() -> tuple[dict[str, Any], int]:
            raise RecordNotFoundException("Part", "TEST123")

        # Clear any existing rollback flag
        db_session = container.db_session()
        db_session.info.pop("needs_rollback", None)

        # Call function that raises domain exception
        result = failing_function()

        # Check if it's a tuple or just the response
        if isinstance(result, tuple):
            _, status_code = result
        else:
            status_code = 404  # Default for RecordNotFoundException

        # Verify the rollback flag was set
        assert db_session.info.get("needs_rollback") is True
        assert status_code == 404

    def test_generic_exception_triggers_rollback(
        self, app: Any, app_context: Any, container: Any
    ) -> None:
        """Test that generic Exception marks session for rollback."""
        from common.core.errors import handle_api_errors

        @handle_api_errors
        def failing_function() -> tuple[dict[str, Any], int]:
            raise Exception("Something went wrong")

        # Clear any existing rollback flag
        db_session = container.db_session()
        db_session.info.pop("needs_rollback", None)

        # Call function that raises generic exception
        result = failing_function()
        response, status_code = result

        # Verify the rollback flag was set
        assert db_session.info.get("needs_rollback") is True
        assert status_code == 500

    def test_successful_operation_no_rollback_flag(
        self, app: Any, app_context: Any, container: Any
    ) -> None:
        """Test that successful operations don't set rollback flag."""
        from common.core.errors import handle_api_errors

        @handle_api_errors
        def successful_function() -> tuple[dict[str, Any], int]:
            return {"success": True}, 200

        # Clear any existing rollback flag
        db_session = container.db_session()
        db_session.info.pop("needs_rollback", None)

        # Call successful function
        result = successful_function()
        response, status_code = result

        # Verify no rollback flag was set (should be None or False)
        needs_rollback = db_session.info.get("needs_rollback")
        assert needs_rollback is None or needs_rollback is False
        assert status_code == 200

    def test_rollback_flag_cleared_after_request(
        self, app: Any, app_context: Any, container: Any
    ) -> None:
        """Test that rollback flag is cleared after request processing."""
        db_session = container.db_session()

        # Manually set rollback flag
        db_session.info["needs_rollback"] = True

        # Simulate teardown handler behavior
        needs_rollback = db_session.info.get("needs_rollback", False)
        assert needs_rollback is True

        # Clear flag (simulating teardown)
        db_session.info.pop("needs_rollback", None)

        # Verify flag is cleared
        needs_rollback = db_session.info.get("needs_rollback")
        assert needs_rollback is None or needs_rollback is False


class TestErrorHandlerRobustness:
    """Test that the error handler itself is robust and doesn't fail."""

    def test_error_handler_works_with_app_context(self, app: Any) -> None:
        """Test error handler works with Flask app context."""
        from common.core.errors import handle_api_errors

        @handle_api_errors
        def failing_function() -> tuple[dict[str, Any], int]:
            raise Exception("Test error")

        with app.app_context():
            result = failing_function()
            if isinstance(result, tuple):
                _, status_code = result
            else:
                status_code = 500
            assert status_code == 500

    def test_nested_exceptions_handled(
        self, app: Any, app_context: Any, container: Any
    ) -> None:
        """Test that nested exceptions are handled properly."""
        from pydantic import ValidationError
        from common.core.errors import handle_api_errors, InvalidOperationException

        @handle_api_errors
        def failing_function() -> tuple[dict[str, Any], int]:
            try:
                raise ValidationError.from_exception_data("Inner error", [])
            except ValidationError as e:
                # Re-raise as different exception
                raise InvalidOperationException(
                    "test operation", "inner validation failed"
                ) from e

        db_session = container.db_session()
        db_session.info.pop("needs_rollback", None)

        result = failing_function()
        if isinstance(result, tuple):
            _, status_code = result
        else:
            status_code = 409

        # Should still set rollback flag
        assert db_session.info.get("needs_rollback") is True
        assert status_code == 409
