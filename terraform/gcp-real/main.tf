# CloudGuardian AI - Real Cloud on GCP (Cloud Run, FREE) (Phase 8)
# -------------------------------------------------------------------
# The genuinely real, non-simulated piece of the multi-cloud story.
# Instead of a manually-managed VM, the monitored service runs as a
# container on Google Cloud Run - managed, serverless, HTTPS by default,
# and it auto-scales DOWN TO ZERO when idle (and back up on demand).
#
# COST: $0 within the Cloud Run free tier when the region is one of
#   us-central1 / us-west1 / us-east1 (2M requests + 180k vCPU-seconds +
#   360k GB-seconds per month). A demo that goes idle between use stays
#   comfortably inside this. It DOES require a GCP project with billing
#   enabled (Cloud Run is "always free up to a limit", not "no card").
#
# How it fits: local Prometheus scrapes this public HTTPS /metrics URL
# exactly like it scrapes the Render deployment - so the dashboard shows
# a service actually running on real Google Cloud.

terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Cloud Run needs the run.googleapis.com API switched on for the project.
resource "google_project_service" "run_api" {
  service            = "run.googleapis.com"
  disable_on_destroy = false
}

resource "google_cloud_run_v2_service" "cloud_service" {
  name     = "cloudguardian-cloud-service"
  location = var.region
  # A small static suffix keeps the URL stable-ish across apply/teardown;
  # the full hostname is printed in outputs.tf (never put secrets here).
  depends_on = [google_project_service.run_api]

  template {
    containers {
      image = var.image
      ports {
        container_port = 8080 # Cloud Run injects PORT=8080; the Dockerfile honors it
      }

      env {
        name  = "SERVICE_NAME"
        value = var.service_name
      }
      env {
        name  = "BASE_CPU"
        value = tostring(var.base_cpu)
      }
      env {
        name  = "BASE_MEM"
        value = tostring(var.base_mem)
      }
      env {
        name  = "BASE_LATENCY_MS"
        value = tostring(var.base_latency_ms)
      }

      # 0.5 vCPU / 512MiB - tiny, cheap, plenty for metric simulation.
      resources {
        limits = {
          cpu    = "0.5"
          memory = "512Mi"
        }
      }
    }

    scaling {
      min_instance_count = 0 # scale-to-zero when idle (this is what keeps it free)
      max_instance_count = 2
    }
    service_account = null # default compute SA is enough for a stateless metric endpoint
  }
}

# Allow anyone to invoke it (public /metrics + /health) - same posture as the
# Render deployment. Note this in the report as "would restrict with IAP/API
# key in production".
resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  location = google_cloud_run_v2_service.cloud_service.location
  name     = google_cloud_run_v2_service.cloud_service.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}