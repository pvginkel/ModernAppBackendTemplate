"""API blueprint registration."""

from flask import Blueprint

# Main API blueprint
api_bp = Blueprint("api", __name__, url_prefix="/api")


from common.auth.routes import register_oidc_routes  # noqa: E402

# Register OIDC auth routes (also sets up authentication check)
register_oidc_routes(api_bp)


# Register your API routes here
# Example:
# from app.api import items as _items  # noqa: F401, E402
