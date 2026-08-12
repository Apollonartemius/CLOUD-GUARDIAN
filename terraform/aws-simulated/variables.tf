variable "bucket_name" {
  description = "Name of the simulated S3 bucket for incident report archival"
  type        = string
  default     = "cloudguardian-incident-reports"
}
