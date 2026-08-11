output "bucket_name" {
  description = "Name of the provisioned (simulated) S3 bucket"
  value       = aws_s3_bucket.incident_reports.bucket
}

output "bucket_endpoint" {
  description = "LocalStack S3 endpoint for this bucket"
  value       = "http://localhost:4566/${aws_s3_bucket.incident_reports.bucket}"
}
