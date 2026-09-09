output "network_name" {
  description = "Name of the Docker network - referenced in docker-compose.yml as an external network"
  value       = docker_network.cloudguardian.name
}

output "fleet_image" {
  description = "Local image name the Kubernetes fleet consumes (import it into k3d with: k3d image import <name> -c cloudguardian)"
  value       = docker_image.simulated_service.name
}