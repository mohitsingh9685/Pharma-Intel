output "state_bucket_name" {
  description = "Bucket used by the staging Terraform backends."
  value       = module.terraform_state_storage.bucket_name
}

output "state_kms_alias" {
  description = "Stable KMS alias used to encrypt Terraform state."
  value       = module.terraform_state_storage.kms_alias_name
}

output "state_kms_key_arn" {
  description = "KMS key ARN used to encrypt Terraform state."
  value       = module.terraform_state_storage.kms_key_arn
}
