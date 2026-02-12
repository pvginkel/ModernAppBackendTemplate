"""CLI commands for database operations."""

import argparse
import sys
from typing import NoReturn

from dotenv import load_dotenv
from flask import Flask

from app import create_app

from app.database import (
    check_db_connection,
    get_current_revision,
    get_pending_migrations,
    upgrade_database,
)

from app.startup import load_test_data_hook, post_migration_hook


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="test-app CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")


    upgrade_parser = subparsers.add_parser(
        "upgrade-db",
        help="Apply database migrations",
    )
    upgrade_parser.add_argument("--recreate", action="store_true")
    upgrade_parser.add_argument("--yes-i-am-sure", action="store_true")

    load_test_data_parser = subparsers.add_parser(
        "load-test-data",
        help="Recreate database and load fixed test data",
    )
    load_test_data_parser.add_argument("--yes-i-am-sure", action="store_true")


    return parser



def handle_upgrade_db(
    app: Flask, recreate: bool = False, confirmed: bool = False
) -> None:
    with app.app_context():
        if not check_db_connection():
            print("Cannot connect to database.", file=sys.stderr)
            sys.exit(1)

        print(f"Using database: {app.config['SQLALCHEMY_DATABASE_URI']}")

        if recreate and not confirmed:
            print("--recreate requires --yes-i-am-sure flag", file=sys.stderr)
            sys.exit(1)

        current_rev = get_current_revision()
        pending = get_pending_migrations()

        if current_rev:
            print(f"Current database revision: {current_rev}")
        else:
            print("Database has no migration version")

        if recreate or pending:
            try:
                applied = upgrade_database(recreate=recreate)
                if applied:
                    print(f"Successfully applied {len(applied)} migration(s)")
                else:
                    print("Database migration completed")
            except Exception as e:
                print(f"Migration failed: {e}", file=sys.stderr)
                sys.exit(1)
        else:
            print("Database is up to date.")

        post_migration_hook(app)


def handle_load_test_data(app: Flask, confirmed: bool = False) -> None:
    with app.app_context():
        if not check_db_connection():
            print("Cannot connect to database.", file=sys.stderr)
            sys.exit(1)

        print(f"Using database: {app.config['SQLALCHEMY_DATABASE_URI']}")

        if not confirmed:
            print("--yes-i-am-sure flag is required", file=sys.stderr)
            sys.exit(1)

        try:
            print("Recreating database from scratch...")
            applied = upgrade_database(recreate=True)
            if applied:
                print(f"Database recreated with {len(applied)} migration(s)")

            load_test_data_hook(app)

        except Exception as e:
            print(f"Failed to load test data: {e}", file=sys.stderr)
            sys.exit(1)



def main() -> NoReturn:
    load_dotenv()

    parser = create_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    app = create_app(skip_background_services=True)


    if args.command == "upgrade-db":
        handle_upgrade_db(
            app=app,
            recreate=args.recreate,
            confirmed=args.yes_i_am_sure,
        )
    elif args.command == "load-test-data":
        handle_load_test_data(
            app=app,
            confirmed=args.yes_i_am_sure,
        )
    else:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        sys.exit(1)


    sys.exit(0)


if __name__ == "__main__":
    main()
