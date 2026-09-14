locals {
  terraform_role_arn = "arn:aws:iam::${var.aws_account_id}:role/${var.terraform_role_name}"
}

module "sales_import_storage" {
  source = "../../modules/secure_s3_bucket"

  aws_account_id              = var.aws_account_id
  aws_region                  = var.aws_region
  bucket_name                 = "pharma-intel-staging-sales-import-${var.aws_account_id}-${var.aws_region}"
  kms_alias_name              = "alias/pharma-intel/staging/sales-import"
  kms_description             = "Encrypts original Pharma Intel staging sales-import files"
  terraform_role_arn          = local.terraform_role_arn
  purpose                     = "SalesImport"
  allow_terraform_data_access = false
  tags = {
    Environment = "staging"
    ManagedBy   = "Terraform"
    Project     = "PharmaIntel"
  }
}
