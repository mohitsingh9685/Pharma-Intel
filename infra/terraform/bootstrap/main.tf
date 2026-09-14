locals {
  terraform_role_arn = "arn:aws:iam::${var.aws_account_id}:role/${var.terraform_role_name}"
}

module "terraform_state_storage" {
  source = "../modules/secure_s3_bucket"

  aws_account_id              = var.aws_account_id
  aws_region                  = var.aws_region
  bucket_name                 = "pharma-intel-staging-tfstate-${var.aws_account_id}-${var.aws_region}"
  kms_alias_name              = "alias/pharma-intel/staging/terraform-state"
  kms_description             = "Encrypts Pharma Intel staging Terraform state"
  terraform_role_arn          = local.terraform_role_arn
  purpose                     = "TerraformState"
  allow_terraform_data_access = true
  tags = {
    Environment = "staging"
    ManagedBy   = "Terraform"
    Project     = "PharmaIntel"
  }
}
