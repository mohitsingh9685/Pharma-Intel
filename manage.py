#!/usr/bin/env python
"""Run Django management commands using this project's configuration."""

import os
import sys


def main() -> None:
    """Select the settings module and pass terminal arguments to Django."""
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Django could not be imported. Activate the project's virtual "
            "environment and install the packages in requirements.txt."
        ) from exc

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
