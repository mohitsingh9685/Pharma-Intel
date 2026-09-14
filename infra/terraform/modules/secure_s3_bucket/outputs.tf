output "bucket_arn" {
  description = "ARN of the private S3 bucket."
  value       = aws_s3_bucket.this.arn
}

output "bucket_name" {
  description = "Name of the private S3 bucket."
  value       = aws_s3_bucket.this.id
}

output "kms_key_arn" {
  description = "ARN of the customer-managed KMS key."
  value       = aws_kms_key.this.arn
}

output "kms_alias_name" {
  description = "Stable KMS alias used by this bucket."
  value       = aws_kms_alias.this.name
}
