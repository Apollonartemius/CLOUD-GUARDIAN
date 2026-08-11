# CloudGuardian AI - Simulated AWS via LocalStack (Phase 6)
# --------------------------------------------------------------
# This provisions a real AWS resource type (S3) using the real AWS
# Terraform provider, but pointed at LocalStack instead of actual AWS.
# LocalStack emulates AWS APIs locally, so this exercises genuine
# multi-cloud Terraform code (same provider you'd use for real AWS)
# without needing an AWS account or incurring any cost.
#
# The bucket represents where CloudGuardian could archive incident
# reports for long-term storage/audit - a realistic use case, even
# though the export logic itself isn't built yet.
#
# Requires the "localstack" container from docker-compose.yml to be
# running (it exposes the emulated AWS API on localhost:4566).

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"

  # LocalStack doesn't check real credentials - these are placeholders,
  # not secrets. Never put real AWS keys in a file like this.
  access_key                  = "test"
  secret_key                  = "test"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true

  endpoints {
    s3 = "http://localhost:4566"
  }
}

resource "aws_s3_bucket" "incident_reports" {
  bucket = var.bucket_name
}

resource "aws_s3_bucket_versioning" "incident_reports" {
  bucket = aws_s3_bucket.incident_reports.id
  versioning_configuration {
    status = "Enabled"
  }
}
