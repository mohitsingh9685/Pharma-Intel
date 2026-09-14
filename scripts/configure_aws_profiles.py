"""Configure the non-secret AWS profiles used by Pharma Intel Terraform."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


SOURCE_PROFILE = "pharma-intel-bootstrap"
ROLE_PROFILE = "pharma-intel-terraform"
REGION = "ap-south-1"


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Configure the Pharma Intel AWS source and role profiles."
    )
    parser.add_argument("--account-id", required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the settings without changing the AWS configuration.",
    )
    return parser.parse_args()


def current_value(profile: str, key: str) -> str:
    result = subprocess.run(
        ["aws", "configure", "get", key, "--profile", profile],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def remove_setting(profile: str, key: str, expected_value: str) -> bool:
    """Remove one known setting without rewriting unrelated AWS profiles."""
    config_path = Path(
        os.environ.get("AWS_CONFIG_FILE", Path.home() / ".aws" / "config")
    ).expanduser()
    if not config_path.exists():
        return False

    lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    section_name = f"profile {profile}"
    current_section = ""
    changed = False
    retained_lines: list[str] = []

    for line in lines:
        section_match = re.fullmatch(r"\s*\[([^]]+)]\s*", line.rstrip("\r\n"))
        if section_match:
            current_section = section_match.group(1).strip()

        setting_match = re.fullmatch(
            rf"\s*{re.escape(key)}\s*=\s*(.*?)\s*", line.rstrip("\r\n")
        )
        if (
            current_section == section_name
            and setting_match
            and setting_match.group(1) == expected_value
        ):
            changed = True
            continue
        retained_lines.append(line)

    if not changed:
        return False

    file_mode = stat.S_IMODE(config_path.stat().st_mode)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=config_path.parent,
        prefix=f".{config_path.name}.",
        delete=False,
    ) as temporary_file:
        temporary_file.writelines(retained_lines)
        temporary_path = Path(temporary_file.name)
    os.chmod(temporary_path, file_mode)
    os.replace(temporary_path, config_path)
    return True


def main() -> int:
    arguments = parse_arguments()
    if re.fullmatch(r"[0-9]{12}", arguments.account_id) is None:
        print("error: --account-id must contain exactly 12 digits", file=sys.stderr)
        return 2
    if shutil.which("aws") is None:
        print("error: AWS CLI is not installed or not on PATH", file=sys.stderr)
        return 1

    mfa_serial = (
        f"arn:aws:iam::{arguments.account_id}:mfa/pharma-intel-terraform-mfa"
    )
    settings = [
        (SOURCE_PROFILE, "region", REGION),
        (SOURCE_PROFILE, "output", "json"),
        (ROLE_PROFILE, "source_profile", SOURCE_PROFILE),
        (
            ROLE_PROFILE,
            "role_arn",
            f"arn:aws:iam::{arguments.account_id}:role/PharmaIntelTerraformRole",
        ),
        (
            ROLE_PROFILE,
            "mfa_serial",
            mfa_serial,
        ),
        (ROLE_PROFILE, "role_session_name", "pharma-intel-terraform"),
        (ROLE_PROFILE, "duration_seconds", "3600"),
        (ROLE_PROFILE, "region", REGION),
        (ROLE_PROFILE, "output", "json"),
    ]

    conflicts = []
    for profile, key, expected in settings:
        existing = current_value(profile, key)
        if existing and existing != expected:
            conflicts.append((profile, key, existing, expected))

    source_mfa_serial = current_value(SOURCE_PROFILE, "mfa_serial")
    if source_mfa_serial and source_mfa_serial != mfa_serial:
        conflicts.append(
            (SOURCE_PROFILE, "mfa_serial", source_mfa_serial, "not configured")
        )

    if conflicts:
        print("error: refusing to replace existing AWS profile values:", file=sys.stderr)
        for profile, key, existing, expected in conflicts:
            print(
                f"  {profile}.{key}: existing={existing!r}, expected={expected!r}",
                file=sys.stderr,
            )
        return 1

    if arguments.dry_run:
        for profile, key, value in settings:
            print(f"would set {profile}.{key}={value}")
        if source_mfa_serial == mfa_serial:
            print(f"would remove legacy {SOURCE_PROFILE}.mfa_serial")
        return 0

    for profile, key, value in settings:
        subprocess.run(
            ["aws", "configure", "set", key, value, "--profile", profile],
            check=True,
        )

    removed_legacy_mfa = remove_setting(SOURCE_PROFILE, "mfa_serial", mfa_serial)

    print(f"Configured AWS profiles: {SOURCE_PROFILE}, {ROLE_PROFILE}.")
    if removed_legacy_mfa:
        print(f"Moved MFA configuration from {SOURCE_PROFILE} to {ROLE_PROFILE}.")
    print("No access keys were written to the AWS configuration file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
