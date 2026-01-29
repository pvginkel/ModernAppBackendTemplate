"""Database health check utilities."""

import logging
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from flask import current_app
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


def _get_alembic_config() -> Config:
    """Get Alembic configuration with database URL from Flask settings."""
    # Assume alembic.ini is in the project root
    alembic_cfg_path = Path(current_app.root_path).parent / "alembic.ini"

    config = Config(str(alembic_cfg_path))

    # Override database URL with current Flask configuration
    db_url = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
    # Convert Flask-SQLAlchemy URL to raw SQLAlchemy URL (remove +psycopg suffix)
    db_url = db_url.replace("+psycopg", "")
    config.set_main_option("sqlalchemy.url", db_url)

    return config


def get_current_revision() -> str | None:
    """Get current database revision from Alembic version table.

    Returns:
        Current revision string or None if no migrations applied
    """
    try:
        with db.engine.connect() as conn:
            result = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
            row = result.fetchone()
            return row[0] if row else None
    except Exception:
        # Table doesn't exist or query failed - treat as no migrations applied
        return None


def get_pending_migrations() -> list[str]:
    """Get list of pending migration revisions.

    Returns:
        List of pending revision IDs (empty if up to date or on error)
    """
    try:
        config = _get_alembic_config()

        with db.engine.connect() as connection:
            config.attributes["connection"] = connection
            script = ScriptDirectory.from_config(config)

            # Get current revision
            current_rev = get_current_revision()

            # Get head revision from script directory (no DB query)
            head_rev = script.get_current_head()

            if not head_rev:
                return []

            if not current_rev:
                # No migrations applied yet, return all from base to head
                revisions = []
                for rev in script.walk_revisions(base="base", head=head_rev):
                    if rev.revision != head_rev:
                        revisions.append(rev.revision)
                revisions.reverse()
                revisions.append(head_rev)
                return revisions

            if current_rev == head_rev:
                return []  # Up to date

            # Get pending revisions between current and head
            revisions = []
            for rev in script.walk_revisions(base=current_rev, head=head_rev):
                if rev.revision != current_rev:
                    revisions.append(rev.revision)

            revisions.reverse()
            return revisions

    except Exception as e:
        logger.warning(f"Failed to check pending migrations: {e}")
        # On any error, treat as no pending migrations (fail safe)
        return []
