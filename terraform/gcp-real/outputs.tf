output "service_url" {
  description = "Public HTTPS URL of the service on Cloud Run (click it to see /health)"
  value       = google_cloud_run_v2_service.cloud_service.uri
}

output "health_check_url" {
  description = "URL for verifying the service is alive"
  value       = "${google_cloud_run_v2_service.cloud_service.uri}/health"
}

output "prometheus_scrape_line" {
  description = "Copy this into monitoring/prometheus/prometheus.yml under scrape_configs"
  value       = <<-EOT
      - job_name: "${var.service_name}"
        scheme: https
        metrics_path: /metrics
        static_configs:
          - targets: ["${replace(google_cloud_run_v2_service.cloud_service.uri, "https://", "")}"]
        scrape_interval: 1m
  EOT
}