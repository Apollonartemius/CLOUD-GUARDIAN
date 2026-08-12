variable "network_name" {
  description = "Docker network shared with the docker-compose platform stack (Prometheus, Grafana, decision-engine, etc). Must match the external network name referenced in docker-compose.yml."
  type        = string
  default     = "cloudguardian-net"
}

variable "docker_host" {
  description = "Docker daemon socket/pipe. Default matches Docker Desktop on Windows with the WSL2 backend (the common setup). On macOS/Linux, override with -var=\"docker_host=unix:///var/run/docker.sock\"."
  type        = string
  default     = "npipe:////./pipe/dockerDesktopLinuxEngine"
}
