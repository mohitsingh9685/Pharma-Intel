"""Render a reviewed IAM policy template without storing the AWS account ID."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


POLICY_TEMPLATES = {
    "bootstrap-user": "bootstrap-user-policy.template.json",
    "terraform-role-trust": "terraform-role-trust-policy.template.json",
    "terraform-role-storage": "terraform-role-storage-policy.template.json",
}


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a Pharma Intel IAM policy template to standard output."
    )
    parser.add_argument("policy", choices=sorted(POLICY_TEMPLATES))
    parser.add_argument("--account-id", required=True)
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    if re.fullmatch(r"[0-9]{12}", arguments.account_id) is None:
        print("error: --account-id must contain exactly 12 digits", file=sys.stderr)
        return 2

    repository_root = Path(__file__).resolve().parents[1]
    template_path = (
        repository_root
        / "infra"
        / "terraform"
        / "iam"
        / POLICY_TEMPLATES[arguments.policy]
    )
    rendered_text = template_path.read_text(encoding="utf-8").replace(
        "<ACCOUNT_ID>", arguments.account_id
    )
    if "<ACCOUNT_ID>" in rendered_text:
        print("error: an account placeholder was not replaced", file=sys.stderr)
        return 1

    policy = json.loads(rendered_text)
    json.dump(policy, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
