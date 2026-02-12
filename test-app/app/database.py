"""Database connection and session management."""

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import MetaData, text
from sqlalchemy.engine import Engine

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.config import Settings
from app.extensions import db

logger = logging.getLogger(__name__)


def get_engine() -> Engine:
    return db.engine


def init_db() -> None:
    import app.models  # noqa: F401
    db.create_all()


def check_db_connection() -> bool:
    try:
        with db.engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            return result.scalar() == 1
    except Exception as e:
        logger.warning(f"Checking database connection failed: {e}")
        return False


def _get_alembic_config() -> Config:
    alembic_cfg_path = Path(__file__).parent.parent / "alembic.ini"
    config = Config(str(alembic_cfg_path))
    settings = Settings.load()
    db_url = settings.database_url.replace("+psycopg", "")
    config.set_main_option("sqlalchemy.url", db_url)
    return config


def get_current_revision() -> str | None:
    try:
        with db.engine.connect() as conn:
            result = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
            row = result.fetchone()
            return row[0] if row else None
    except Exception:
        return None


def get_pending_migrations() -> list[str]:
    try:
        config = _get_alembic_config()
        with db.engine.connect() as connection:
            config.attributes["connection"] = connection
            script = ScriptDirectory.from_config(config)
            current_rev = get_current_revision()
            head_rev = script.get_current_head()

            if not head_rev:
                return []
            if not current_rev:
                revisions = []
                for rev in script.walk_revisions(base="base", head=head_rev):
                    if rev.revision != head_rev:
                        revisions.append(rev.revision)
                revisions.reverse()
                revisions.append(head_rev)
                return revisions
            if current_rev == head_rev:
                return []

            revisions = []
            for rev in script.walk_revisions(base=current_rev, head=head_rev):
                if rev.revision != current_rev:
                    revisions.append(rev.revision)
            revisions.reverse()
            return revisions
    except Exception:
        return []


def drop_all_tables() -> None:
    metadata = MetaData()
    metadata.reflect(bind=db.engine)
    metadata.drop_all(bind=db.engine)
    db.metadata.clear()


def _get_migration_info(script_dir: ScriptDirectory, revision: str) -> tuple[str, str]:
    try:
        rev_obj = script_dir.get_revision(revision)
        if not rev_obj or not rev_obj.path:
            return revision, "Unknown migration"
        migration_file = Path(rev_obj.path)
        if not migration_file.exists():
            return revision, "Migration file not found"
        content = migration_file.read_text()
        docstring_match = re.search(r'"""([^"]+)"""', content)
        if docstring_match:
            description = docstring_match.group(1).strip()
            return revision[:7], description
        return revision[:7], "Migration"
    except Exception:
        return revision[:7], "Migration"


def upgrade_database(recreate: bool = False) -> list[tuple[str, str]]:
    engine = db.engine
    is_sqlite = engine.dialect.name == "sqlite"

    if recreate and is_sqlite:
        print("SQLite detected - rebuilding schema using SQLAlchemy metadata")
        metadata = MetaData()
        metadata.reflect(bind=engine)
        if metadata.tables:
            metadata.drop_all(bind=engine)
        db.create_all()
        config = _get_alembic_config()
        with engine.connect() as connection:
            config.attributes["connection"] = connection
            command.stamp(config, "head")
        return []

    config = _get_alembic_config()
    applied_migrations: list[tuple[str, str]] = []

    with db.engine.connect() as connection:
        config.attributes["connection"] = connection
        script = ScriptDirectory.from_config(config)

        if recreate:
            drop_all_tables()

        pending = get_pending_migrations()
        if not pending:
            return applied_migrations

        for revision in pending:
            rev_short, description = _get_migration_info(script, revision)
            try:
                command.upgrade(config, revision)
                applied_migrations.append((rev_short, description))
            except Exception as e:
                print(f"Failed to apply migration {rev_short}: {e}")
                raise

        return applied_migrations
