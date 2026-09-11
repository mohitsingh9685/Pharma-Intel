"""Add private local database configuration without replacing existing secrets."""

import fcntl
from io import StringIO
import os
from pathlib import Path
import secrets
import tempfile

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = PROJECT_ROOT / ".env"


def configure_database() -> None:
    """Append missing settings while preserving the existing local signing key."""
    if ENV_PATH.is_symlink():
        raise SystemExit("Refusing to modify a symlinked .env file.")
    if not ENV_PATH.is_file():
        raise SystemExit("Create .env first with: python scripts/create_local_env.py")

    original = ENV_PATH.read_text(encoding="utf-8")
    existing = dotenv_values(stream=StringIO(original), interpolate=False)
    if not (existing.get("DJANGO_SECRET_KEY") or "").strip():
        raise SystemExit("The existing .env needs a nonblank DJANGO_SECRET_KEY.")

    expected_names = {
        "DATABASE_NAME": "pharma_intel",
        "DATABASE_USER": "pharma_app",
        "DATABASE_HOST": "127.0.0.1",
    }
    for name, expected in expected_names.items():
        if name in existing and existing[name] != expected:
            raise SystemExit(
                f"Existing {name} differs from this local Compose setup. "
                "Review the configuration before continuing; no values were changed."
            )

    defaults = {
        **expected_names,
        "DATABASE_PORT": "5433",
        "DATABASE_PASSWORD": secrets.token_urlsafe(48),
        "POSTGRES_ADMIN_PASSWORD": secrets.token_urlsafe(48),
    }
    for name in defaults:
        if name in existing and not (existing[name] or "").strip():
            raise SystemExit(f"Existing {name} is blank; no values were changed.")

    port = existing.get("DATABASE_PORT", "5433")
    if not port.isascii() or not port.isdigit() or not 1 <= int(port) <= 65535:
        raise SystemExit("DATABASE_PORT must be an integer from 1 to 65535.")

    additions = {name: value for name, value in defaults.items() if name not in existing}
    # This is an explicit local setup action, never application startup logic.
    ENV_PATH.chmod(0o600)
    if additions:
        content = original + "\n# Local PostgreSQL configuration\n"
        content += "".join(f"{name}={value}\n" for name, value in additions.items())
        temporary_path = None
        try:
            # A private temporary file and atomic replacement prevent Django
            # from reading a half-written configuration if writing fails.
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=PROJECT_ROOT,
                prefix=".env.", delete=False,
            ) as env_file:
                temporary_path = Path(env_file.name)
                env_file.write(content)
                env_file.flush()
                os.fsync(env_file.fileno())
            if ENV_PATH.is_symlink() or ENV_PATH.read_text(encoding="utf-8") != original:
                raise SystemExit(".env changed during setup; no replacement was made. Run setup again.")
            os.replace(temporary_path, ENV_PATH)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        print("Added missing local database settings to .env; existing values were kept.")
    else:
        print("Local database settings already exist; existing values were kept.")


def main() -> None:
    """Serialize setup runs on macOS/Linux so simultaneous runs keep one key set."""
    descriptor = os.open(
        PROJECT_ROOT / ".env.lock",
        os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        configure_database()


if __name__ == "__main__":
    main()
