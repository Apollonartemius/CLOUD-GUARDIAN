variable "project_id" {
  description = "Your GCP project ID (find it on the GCP Console dashboard, or run `gcloud config get-value project`)"
  type        = string
}

variable "region" {
  description = "Must be us-central1, us-west1, or us-east1 to stay within the Cloud Run free tier"
  type        = string
  default     = "us-central1"
}

variable "image" {
  description = "Container image to run. Public Docker Hub repo is simplest - push your local image with: docker tag cloudguardian-ai-simulated-service:latest <dockerhub-user>/cloudguardian-simulated-service:latest && docker push <dockerhub-user>/cloudguardian-simulated-service:latest"
  type        = string
  default     = "docker.io/library/cloudguardian-simulated-service:latest"
}

variable "service_name" {
  description = "Name this service reports as in its metrics (label seen in Grafana/Prometheus)"
  type        = string
  default     = "cloud-service-gcp"
}

variable "base_cpu" {
  description = "Baseline CPU percentage the simulated service reports"
  type        = number
  default     = 18
}

variable "base_mem" {
  description = "Baseline memory MB the simulated service reports"
  type        = number
  default     = 280
}

variable "base_latency_ms" {
  description = "Baseline request latency in milliseconds the simulated service reports"
  type        = number
  default     = 55
}