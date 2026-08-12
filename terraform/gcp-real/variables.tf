variable "project_id" {
  description = "Your GCP project ID (find it on the GCP Console dashboard, or run `gcloud config get-value project`)"
  type        = string
}

variable "region" {
  description = "Must be us-west1, us-central1, or us-east1 to stay within the Always Free e2-micro eligibility"
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "A zone within the chosen region"
  type        = string
  default     = "us-central1-a"
}

variable "service_name" {
  description = "Name this service reports as in its metrics"
  type        = string
  default     = "cloud-service-gcp"
}

variable "base_cpu" {
  type    = number
  default = 18
}

variable "base_mem" {
  type    = number
  default = 280
}

variable "base_latency" {
  type    = number
  default = 55
}
