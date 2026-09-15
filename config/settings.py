"""Django configuration for local development with PostgreSQL."""

import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent

# Read local configuration from the project root, regardless of the working
# directory. Existing process variables take priority; values remain literal.
load_dotenv(BASE_DIR / ".env", override=False, interpolate=False)


def boolean_setting(name: str, *, default: bool) -> bool:
    """Read an explicit boolean setting and reject ambiguous values."""
    value = os.environ.get(name)
    if value is None:
        return default

    normalized_value = value.strip().lower()
    if normalized_value in {"1", "true", "yes", "on"}:
        return True
    if normalized_value in {"0", "false", "no", "off"}:
        return False
    raise ImproperlyConfigured(
        f"{name} must be one of: true, false, 1, 0, yes, no, on, off."
    )

# Read the signing key after loading local configuration. Never generate one
# during application startup: all processes must keep using the same key.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY.strip():
    raise ImproperlyConfigured(
        "Set DJANGO_SECRET_KEY in the environment or the project-root .env file. "
        "For first-time local setup, run: python scripts/create_local_env.py"
    )

DEBUG = boolean_setting("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.postgres",
    "django.contrib.staticfiles",
    "accounts.apps.AccountsConfig",
    "master_data.apps.MasterDataConfig",
    "business_data.apps.BusinessDataConfig",
    "sales_imports.apps.SalesImportsConfig",
]

AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]
WSGI_APPLICATION = "config.wsgi.application"

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": (
            "django.contrib.auth.password_validation."
            "UserAttributeSimilarityValidator"
        )
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"
    },
]

def required_setting(name: str) -> str:
    """Require connection settings rather than silently connecting elsewhere."""
    value = os.environ.get(name, "")
    if not value.strip():
        raise ImproperlyConfigured(
            f"Set {name} in the environment or .env. "
            "For local setup, run: python scripts/configure_local_database.py"
        )
    return value


database_port = required_setting("DATABASE_PORT")
if (
    not database_port.isascii()
    or not database_port.isdigit()
    or not 1 <= int(database_port) <= 65535
):
    raise ImproperlyConfigured("DATABASE_PORT must be an integer from 1 to 65535.")

# Django uses its own database role, not the container's administrator account.
# Connections are closed after requests for this local setup. Deployment pooling
# and capacity limits will be configured against the agreed workload.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": required_setting("DATABASE_NAME"),
        "USER": required_setting("DATABASE_USER"),
        "PASSWORD": required_setting("DATABASE_PASSWORD"),
        "HOST": required_setting("DATABASE_HOST"),
        "PORT": database_port,
        "CONN_MAX_AGE": 0,
        "OPTIONS": {"connect_timeout": 5},
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "UTC").strip()
try:
    ZoneInfo(TIME_ZONE)
except ZoneInfoNotFoundError as error:
    raise ImproperlyConfigured(
        f"DJANGO_TIME_ZONE is not a recognized IANA time zone: {TIME_ZONE!r}."
    ) from error
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"


def positive_integer_setting(name: str, *, default: int) -> int:
    """Read a positive integer setting and reject unsafe values."""
    raw_value = os.environ.get(name, str(default)).strip()
    if not raw_value.isascii() or not raw_value.isdigit() or int(raw_value) < 1:
        raise ImproperlyConfigured(f"{name} must be a positive integer.")
    return int(raw_value)


# This is an interim safety ceiling, not a measured production capacity target.
SALES_IMPORT_MAX_UPLOAD_BYTES = positive_integer_setting(
    "SALES_IMPORT_MAX_UPLOAD_BYTES",
    default=25 * 1024 * 1024,
)
SALES_IMPORT_RECONCILE_AFTER_MINUTES = positive_integer_setting(
    "SALES_IMPORT_RECONCILE_AFTER_MINUTES",
    default=15,
)
SALES_IMPORT_MAX_ROWS = positive_integer_setting(
    "SALES_IMPORT_MAX_ROWS",
    default=250_000,
)

# Files over 1 MiB are spooled to disk instead of occupying web-worker memory.
FILE_UPLOAD_MAX_MEMORY_SIZE = min(SALES_IMPORT_MAX_UPLOAD_BYTES, 1024 * 1024)
# This endpoint accepts one original file. Reject extra multipart file parts
# before Django spends disk and parsing work on fields the form will ignore.
DATA_UPLOAD_MAX_NUMBER_FILES = 1
FILE_UPLOAD_HANDLERS = [
    "sales_imports.upload_handlers.BoundedFileUploadHandler",
    "django.core.files.uploadhandler.MemoryFileUploadHandler",
    "django.core.files.uploadhandler.TemporaryFileUploadHandler",
]

SALES_IMPORT_AWS_REGION = os.environ.get(
    "SALES_IMPORT_AWS_REGION",
    "ap-south-1",
).strip()
SALES_IMPORT_S3_BUCKET = os.environ.get("SALES_IMPORT_S3_BUCKET", "").strip()
SALES_IMPORT_S3_KMS_KEY_ARN = os.environ.get(
    "SALES_IMPORT_S3_KMS_KEY_ARN",
    "",
).strip()
