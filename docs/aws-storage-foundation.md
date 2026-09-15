# AWS storage foundation

## Purpose

Original CSV and Excel uploads are stored as private objects in Amazon S3.
PostgreSQL stores their immutable intake records and will store validation
errors, row lineage, and accepted business records as ingestion is added.
Keeping the original file lets an administrator audit or reprocess an import
without treating a large binary file as a database row.

This milestone prepares staging infrastructure in `ap-south-1`. The Django
sales-import intake now implements the S3 client contract; deployment still
needs a dedicated application/worker runtime role after the hosting target is
selected.

The Terraform-state and sales-import stacks were applied successfully on
2026-09-14. Bootstrap state now uses the encrypted S3 backend, and a subsequent
plan found no differences between the bootstrap configuration and AWS. Django
runtime access remains intentionally unconfigured until the deployment target
is selected. The intake now requires narrowly scoped `PutObject` and versioned
`HeadObject` access, including checksum retrieval, plus the matching KMS data-key,
encrypt, and decrypt operations for this bucket only.

## Access design

```text
Local operator
  -> pharma-intel-terraform-bootstrap IAM user
  -> MFA
  -> PharmaIntelTerraformRole temporary session
  -> Terraform-managed staging resources
```

The bootstrap user has only `sts:AssumeRole` permission. The role trust policy
names that exact user and requires MFA. The role's storage policy is limited to
two deterministic bucket names and tagged Pharma Intel KMS keys. It cannot read
the sales-import objects, schedule key deletion, or delete either bucket.

The files under `infra/terraform/iam/` are the reviewed source for the three
manually bootstrapped IAM policies. Replace `<ACCOUNT_ID>` inside the AWS console;
never put access keys or MFA values in these files.

`scripts/render_aws_policy.py` safely replaces every account placeholder and
prints valid JSON. On macOS its output can be piped to `pbcopy` and pasted into
the IAM JSON editor without creating a credential file.

`scripts/configure_aws_profiles.py` writes only non-secret profile metadata to
the standard AWS configuration file. It refuses to replace conflicting values;
the access key remains in the macOS Keychain managed by `aws-vault`.

## Resources

The bootstrap stack creates:

- one private, versioned S3 bucket for Terraform state;
- one customer-managed KMS key used only for Terraform state;
- the configuration required for S3-native state locking. Terraform creates and
  removes the `.tflock` object while a remote-state operation is running.

The staging stack creates:

- one private, versioned S3 bucket for original sales-import files;
- a separate customer-managed KMS key for those business files.

Both buckets block public access, reject non-TLS requests, disable ACLs, require
their exact KMS key for object writes, use S3 Bucket Keys, and abort unfinished
multipart uploads after seven days. Clients must send the KMS encryption headers
when uploading. The buckets do not delete objects or older versions automatically
because the business and legal retention period has not been decided.

Each original object records the immutable import identifier, SHA-256 digest,
`sales_v1` intake-envelope version, and `sales_rows_v1` row-contract version in
S3 metadata. Reconciliation verifies those values before an uncertain upload is
accepted as received. S3 retains the bytes; contract parsing and structured row
state belong to the application and PostgreSQL.

Terraform state and sales uploads use separate encryption keys. Access to state
therefore does not grant access to uploaded business data. A later application
runtime role will receive narrow upload and verification permissions for the
sales bucket. The Terraform identity is not used by Django.

## State bootstrap

The state bucket must exist before Terraform can use it. Therefore the bootstrap
stack starts with local state. After its first successful apply, copy
`backend.tf.example` to the ignored `backend.tf`, create a private backend config
from `backend.s3.tfbackend.example`, and run Terraform's state migration. The
staging stack uses that remote backend from its first run.

Local `.tfstate`, private `.tfvars`, backend configuration, plans, and Terraform
working directories are excluded from Git. `.terraform.lock.hcl` is intentionally
not ignored and must be committed after provider installation is verified.

The ordered bootstrap is:

1. verify that `PharmaIntelTerraformRole` trusts only the bootstrap-user ARN and
   requires `aws:MultiFactorAuthPresent`;
2. attach the reviewed storage policy to that role, not to the IAM user;
3. create one IAM access key and store it in macOS Keychain through `aws-vault`;
4. configure a Keychain-backed source profile and put `mfa_serial` on the role
   profile that performs `sts:AssumeRole`;
5. initialize and apply the bootstrap stack using local state;
6. migrate the bootstrap state into the new state bucket;
7. initialize and apply the staging stack through the remote backend.

Every Terraform plan must be reviewed before apply. The two customer-managed
KMS keys have a small ongoing AWS charge even when the buckets are empty.

## Decisions still required

- retention duration for original uploads and old versions;
- maximum upload size and expected upload frequency;
- background queue and worker runtime;
- deployed Django/worker identity that may use the sales-import key;
- production account and production retention/recovery requirements.

The current account is accepted for staging and initial deployment. A separately
owned production AWS account remains the planned boundary after funding.
