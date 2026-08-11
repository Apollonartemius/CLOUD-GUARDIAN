# CloudGuardian AI - Real Cloud Infrastructure on GCP (Phase 6)
# -------------------------------------------------------------------
# This is the ONE genuinely real (non-simulated) piece of the
# "multi-cloud" story: an actual e2-micro VM on Google Cloud's Always
# Free tier, running the exact same simulated-service code that runs
# locally in Docker - just on real cloud infrastructure instead of
# your laptop.
#
# Cost: $0, as long as you stay within the Always Free limits -
# 1 e2-micro instance in us-west1/us-central1/us-east1, which is
# exactly what this provisions. See the main README for account
# setup and a cost-safety checklist before running this.

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

# Reserved (not ephemeral) so the IP doesn't change if the VM is
# recreated - lets you hardcode it once in prometheus.yml.
resource "google_compute_address" "static_ip" {
  name   = "cloudguardian-static-ip"
  region = var.region
}

resource "google_compute_firewall" "allow_service_port" {
  name    = "cloudguardian-allow-8000"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["8000"]
  }

  # Open to the internet so your local Prometheus can scrape it - fine
  # for a learning project, but note this in your report as something
  # you'd lock down (e.g. to your own IP) in a real deployment.
  source_ranges = ["0.0.0.0/0"]
  target_tags   = ["cloudguardian-service"]
}

resource "google_compute_instance" "cloud_service" {
  name         = "cloudguardian-cloud-service"
  machine_type = "e2-micro" # Always Free eligible machine type
  zone         = var.zone

  tags = ["cloudguardian-service"]

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 10 # GB - well within the 30GB Always Free disk allowance
    }
  }

  network_interface {
    network = "default"
    access_config {
      nat_ip = google_compute_address.static_ip.address
    }
  }

  metadata_startup_script = templatefile("${path.module}/startup-script.sh.tpl", {
    main_py_content = file("${path.module}/../../services/simulated-service/main.py")
    service_name    = var.service_name
    base_cpu        = var.base_cpu
    base_mem        = var.base_mem
    base_latency    = var.base_latency
  })
}
