"""Create private local configuration once, without overwriting existing files."""

import os
from pathlib import Path
import secrets


def main() -> None:
    """Generate a persistent development signing key without displaying it."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    content = (
        "# Local development configuration. Do not commit this file.\n"
        f"DJANGO_SECRET_KEY={secrets.token_urlsafe(64)}\n"
        "DJANGO_DEBUG=true\n"
    )

    try:
        descriptor = os.open(
            env_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        raise SystemExit(
            ".env already exists and was left unchanged. "
            "Use the existing configuration instead of generating a new key."
        ) from None

    # On macOS/Linux, 0o600 permits only the owner to read and write the file.
    with os.fdopen(descriptor, "w", encoding="utf-8") as env_file:
        env_file.write(content)

    print("Created .env with a private local development key.")


if __name__ == "__main__":
    main()
