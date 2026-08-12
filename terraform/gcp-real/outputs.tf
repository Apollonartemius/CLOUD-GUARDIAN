output "external_ip" {
  description = "Public IP of the real GCP VM"
  value       = google_compute_address.static_ip.address
}

output "service_url" {
  description = "URL of the service running on the real GCP VM"
  value       = "http://${google_compute_address.static_ip.address}:8000"
}

output "prometheus_scrape_line" {
  description = "Copy this into monitoring/prometheus/prometheus.yml under scrape_configs"
  value       = <<-EOT
    - job_name: "${var.service_name}"
      static_configs:
        - targets: ["${google_compute_address.static_ip.address}:8000"]
  EOT
}
