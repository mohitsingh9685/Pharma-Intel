"""Local test settings that use a dedicated database-creator role."""

from .settings import *  # noqa: F403
from .settings import DATABASES, required_setting


DATABASES["default"] = {
    **DATABASES["default"],
    "USER": required_setting("DATABASE_TEST_USER"),
    "PASSWORD": required_setting("DATABASE_TEST_PASSWORD"),
    "TEST": {"NAME": "test_pharma_intel"},
}

