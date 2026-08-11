variable "network_name" {
  description = "Docker network shared with the docker-compose platform stack (Prometheus, Grafana, decision-engine, etc). Must match the external network name referenced in docker-compose.yml."
  type        = string
  default     = "cloudguardian-net"
}
