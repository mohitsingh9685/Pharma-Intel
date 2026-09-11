"""Create a local-only PostgreSQL role allowed to create test databases."""

from __future__ import annotations

import fcntl
import os
import secrets
import stat
import tempfile
from pathlib import Path

import psycopg
from dotenv import dotenv_values
from psycopg import sql


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"
LOCK_PATH = PROJECT_ROOT / ".env.lock"
TEST_ROLE = "pharma_test_runner"


def append_missing_settings(values: dict[str, str]) -> None:
    if ENV_PATH.is_symlink():
        raise RuntimeError("Refusing to update .env because it is a symbolic link.")
    if not ENV_PATH.exists():
        raise RuntimeError("Missing .env. Complete the local database setup first.")

    LOCK_PATH.touch(mode=0o600, exist_ok=True)
    with LOCK_PATH.open("r+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

        current = ENV_PATH.read_text(encoding="utf-8")
        parsed = dotenv_values(ENV_PATH, interpolate=False)
        missing = {key: value for key, value in values.items() if not parsed.get(key)}
        if not missing:
            return

        separator = "" if not current or current.endswith("\n") else "\n"
        addition = "\n# Dedicated local test database role.\n" + "".join(
            f"{key}={value}\n" for key, value in missing.items()
        )
        updated = current + separator + addition

        descriptor, temporary_name = tempfile.mkstemp(
            dir=PROJECT_ROOT,
            prefix=".env.",
            text=True,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as temporary_file:
                temporary_file.write(updated)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            temporary_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            os.replace(temporary_path, ENV_PATH)
        finally:
            temporary_path.unlink(missing_ok=True)


def required(values: dict[str, str | None], name: str) -> str:
    value = values.get(name)
    if not value:
        raise RuntimeError(f"Missing {name} in .env.")
    return value


def main() -> None:
    append_missing_settings(
        {
            "DATABASE_TEST_USER": TEST_ROLE,
            "DATABASE_TEST_PASSWORD": secrets.token_urlsafe(48),
        }
    )
    values = dotenv_values(ENV_PATH, interpolate=False)

    test_user = required(values, "DATABASE_TEST_USER")
    if test_user != TEST_ROLE:
        raise RuntimeError(f"DATABASE_TEST_USER must be {TEST_ROLE!r} locally.")

    connection = psycopg.connect(
        dbname="postgres",
        user="postgres",
        password=required(values, "POSTGRES_ADMIN_PASSWORD"),
        host=required(values, "DATABASE_HOST"),
        port=required(values, "DATABASE_PORT"),
        connect_timeout=5,
        autocommit=True,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (test_user,))
            role_exists = cursor.fetchone() is not None
            password = required(values, "DATABASE_TEST_PASSWORD")

            if role_exists:
                statement = sql.SQL(
                    "ALTER ROLE {} WITH LOGIN CREATEDB NOSUPERUSER "
                    "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(sql.Identifier(test_user), sql.Literal(password))
            else:
                statement = sql.SQL(
                    "CREATE ROLE {} WITH LOGIN CREATEDB NOSUPERUSER "
                    "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(sql.Identifier(test_user), sql.Literal(password))

            cursor.execute(statement)
            cursor.execute(
                sql.SQL("GRANT CONNECT ON DATABASE postgres TO {}").format(
                    sql.Identifier(test_user)
                )
            )
    finally:
        connection.close()

    print("Configured the dedicated local PostgreSQL test role.")
    print("Application database permissions were not changed.")


if __name__ == "__main__":
    main()
