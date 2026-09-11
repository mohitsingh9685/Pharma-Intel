"""Verify a real Django connection to the configured local PostgreSQL server."""

import os
from pathlib import Path
import sys


# Direct script execution otherwise places only scripts/ on the import path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
from django.conf import settings
from django.db import DatabaseError, connection


def main() -> None:
    """Check database identity, permissions, and a rolled-back write operation."""
    django.setup()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_database(), current_user, "
                "current_setting('server_version'), "
                "current_setting('server_version_num')::integer, rolsuper, rolcreatedb, "
                "rolcreaterole, rolreplication, rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user"
            )
            database_name, username, version, version_number, *privileges = cursor.fetchone()

        configured = settings.DATABASES["default"]
        if version_number < 180000:
            raise SystemExit(
                "This Compose volume layout requires PostgreSQL 18 or newer. "
                "Review the image and storage configuration before continuing."
            )
        if database_name != configured["NAME"] or username != configured["USER"]:
            raise SystemExit("Database identity did not match Django's configuration.")
        if any(privileges):
            raise SystemExit("The application role has unexpected administrative privileges.")

        # A temporary table verifies write access without creating application
        # tables. The transaction is rolled back even after a successful check.
        from django.db import transaction

        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "CREATE TEMPORARY TABLE pharma_connection_check "
                    "(value integer NOT NULL) ON COMMIT DROP"
                )
                cursor.execute("INSERT INTO pharma_connection_check (value) VALUES (%s)", [1])
                cursor.execute("SELECT value FROM pharma_connection_check")
                if cursor.fetchone() != (1,):
                    raise SystemExit("Database write/read verification failed.")
            transaction.set_rollback(True)

        print(f"Connected to PostgreSQL {version}.")
        print(f"Database: {database_name}; application role: {username}.")
        print("Administrative privileges: disabled.")
        print("Temporary write/read check: passed and rolled back.")
    except DatabaseError as exc:
        # Avoid displaying a connection string or credentials in diagnostics.
        raise SystemExit(
            f"Database check failed ({type(exc).__name__}). "
            "Check Docker's database status and the local connection settings."
        ) from None
    finally:
        connection.close()


if __name__ == "__main__":
    main()
