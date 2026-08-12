# CloudGuardian AI - Monitored Infrastructure (Phase 6)
# ---------------------------------------------------------
# This provisions the 3 "monitored" microservices (auth, payment,
# inventory) as real infrastructure-as-code, instead of them being
# manually defined in docker-compose.yml like in Phases 1-5.
#
# Why split this out from the rest of the stack: in a real
# organization, the *platform* (Prometheus, Grafana, the anomaly
# detector, the decision engine) is usually owned and provisioned
# separately from the *workloads* it monitors. This mirrors that:
# Terraform owns the fleet being watched, docker-compose still owns
# the observability/self-healing platform, and they're connected by
# a shared Docker network created here and referenced as "external"
# in docker-compose.yml.

terraform {
  required_providers {
    docker = {
      source  = "kreuzwerker/docker"
      version = "~> 3.0"
    }
  }
}

provider "docker" {
  # The kreuzwerker/docker provider doesn't always autodetect Docker
  # Desktop's Windows named pipe correctly - on the common WSL2-backend
  # setup, the actual pipe is "dockerDesktopLinuxEngine", not the
  # provider's default "docker_engine". Without this, apply fails with
  # a confusing "elevated privileges" / "system cannot find the file
  # specified" error even though Docker Desktop is running fine.
  host = var.docker_host
}

resource "docker_network" "cloudguardian" {
  name = var.network_name
}

# Built once from the same source used in earlier phases - Terraform
# just becomes the thing that builds and manages it instead of
# docker-compose.
resource "docker_image" "simulated_service" {
  name = "cloudguardian-ai-simulated-service:latest"
  build {
    context = "${path.module}/../../services/simulated-service"
  }
  keep_locally = true
}

locals {
  services = {
    "auth-service" = {
      base_cpu     = 15
      base_mem     = 250
      base_latency = 40
      host_port    = 8001
    }
    "payment-service" = {
      base_cpu     = 25
      base_mem     = 400
      base_latency = 80
      host_port    = 8002
    }
    "inventory-service" = {
      base_cpu     = 20
      base_mem     = 300
      base_latency = 60
      host_port    = 8003
    }
  }
}

resource "docker_container" "service" {
  for_each = local.services

  name  = each.key
  image = docker_image.simulated_service.image_id

  env = [
    "SERVICE_NAME=${each.key}",
    "BASE_CPU=${each.value.base_cpu}",
    "BASE_MEM=${each.value.base_mem}",
    "BASE_LATENCY_MS=${each.value.base_latency}",
  ]

  ports {
    internal = 8000
    external = each.value.host_port
  }

  networks_advanced {
    name = docker_network.cloudguardian.name
  }

  restart = "unless-stopped"
}
