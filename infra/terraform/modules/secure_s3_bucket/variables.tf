variable "aws_account_id" {
  description = "AWS account that owns the bucket and encryption key."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must contain exactly 12 digits."
  }
}

variable "aws_region" {
  description = "AWS region containing the bucket and KMS key."
  type        = string
}

variable "bucket_name" {
  description = "Globally unique S3 bucket name."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.bucket_name))
    error_message = "bucket_name must be a valid 3-63 character S3 bucket name."
  }
}

variable "kms_alias_name" {
  description = "KMS alias including the alias/ prefix."
  type        = string

  validation {
    condition     = startswith(var.kms_alias_name, "alias/pharma-intel/")
    error_message = "kms_alias_name must start with alias/pharma-intel/."
  }
}

variable "kms_description" {
  description = "Human-readable purpose of the KMS key."
  type        = string
}

variable "terraform_role_arn" {
  description = "ARN of the role allowed to maintain this key."
  type        = string
}

variable "purpose" {
  description = "Stable Purpose tag used by IAM conditions."
  type        = string
}

variable "allow_terraform_data_access" {
  description = "Whether Terraform may use this key for encrypted state objects."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Required ownership tags applied to every resource."
  type        = map(string)
}
