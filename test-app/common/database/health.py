"""Database health check utilities."""

import logging

from sqlalchemy import text

from common.database.extensions import db

logger = logging.getLogger(__name__)


def check_db_connection() -> bool:
    """Check if the database connection is healthy.

    Returns:
        True if database is reachable, False otherwise
    """
    try:
        with db.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return False
