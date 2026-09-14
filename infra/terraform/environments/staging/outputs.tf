output "sales_import_bucket_arn" {
  description = "ARN used later by the Django and worker runtime policies."
  value       = module.sales_import_storage.bucket_arn
}

output "sales_import_bucket_name" {
  description = "Private bucket containing original uploaded sales files."
  value       = module.sales_import_storage.bucket_name
}

output "sales_import_kms_key_arn" {
  description = "KMS key that encrypts original sales-import objects."
  value       = module.sales_import_storage.kms_key_arn
}
