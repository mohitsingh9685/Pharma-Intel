variable "aws_account_id" {
  description = "Expected AWS account ID; Terraform refuses a different account."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must contain exactly 12 digits."
  }
}

variable "aws_region" {
  description = "AWS region for the staging deployment."
  type        = string
  default     = "ap-south-1"

  validation {
    condition     = var.aws_region == "ap-south-1"
    error_message = "The accepted staging region is ap-south-1."
  }
}

variable "terraform_role_name" {
  description = "Existing MFA-protected role used to run Terraform."
  type        = string
  default     = "PharmaIntelTerraformRole"
}
