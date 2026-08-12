output "service_urls" {
  description = "Local URLs for each monitored service"
  value = {
    for name, cfg in local.services :
    name => "http://localhost:${cfg.host_port}"
  }
}

output "network_name" {
  description = "Name of the Docker network - reference this in docker-compose.yml as an external network"
  value       = docker_network.cloudguardian.name
}
