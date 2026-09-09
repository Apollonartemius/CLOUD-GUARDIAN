# CloudGuardian AI - Shared local infrastructure (Phase 6, revised Phase 7)
# -------------------------------------------------------------------------
# This workspace provides the shared glue the platform stack plugs into:
#   * the external Docker network (`cloudguardian-net`) that docker-compose
#     references as "external: true"
#   * the local `cloudguardian-ai-simulated-service:latest` image used by
#     the monitored fleet
#
# The 3 monitored services (auth, payment, inventory) themselves now run as
# real Kubernetes Deployments on the local k3d cluster (see k8s/). They are
# intentionally NOT defined here anymore - ports 8001-8003 belong to the
# k3d NodePort mappings, so defining them as docker containers would collide.
#
# Startup order (see README Part 3/4):
#   1  terraform apply          -> network + local image (this file)
#   2  k3d image import         -> ship the image into the k3d nodes
#   3  kubectl apply -f k8s/    -> the fleet (Deployments, Services, HPAs)
#   4  docker compose up        -> the platform stack

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

# Built from the same source used in earlier phases - Terraform becomes the
# thing that builds/manages it instead of docker-compose.
resource "docker_image" "simulated_service" {
  name = "cloudguardian-ai-simulated-service:latest"
  build {
    context = "${path.module}/../../services/simulated-service"
  }
  keep_locally = true
}