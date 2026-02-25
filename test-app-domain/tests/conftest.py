"""Pytest configuration and fixtures for domain tests.

Infrastructure fixtures (app, client, session, OIDC, SSE) from
conftest_infrastructure.py. Add app-specific domain fixtures below.
"""

# Import all infrastructure fixtures
from tests.conftest_infrastructure import *  # noqa: F403
