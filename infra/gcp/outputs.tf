output "lb_ip" {
  description = "Global IP of the load balancer. Point the domain's A record here."
  value       = google_compute_global_address.lb.address
}

output "url" {
  description = "Public URL of the app."
  value       = local.https_enabled ? "https://${var.domain}" : "http://${google_compute_global_address.lb.address}"
}

output "dns_records" {
  description = "DNS records to create for the domain: the A record (traffic) and the CNAME that authorizes the managed certificate."
  value = local.https_enabled ? {
    a = {
      name = "${var.domain}."
      type = "A"
      data = google_compute_global_address.lb.address
    }
    cert_cname = {
      name = google_certificate_manager_dns_authorization.app[0].dns_resource_record[0].name
      type = google_certificate_manager_dns_authorization.app[0].dns_resource_record[0].type
      data = google_certificate_manager_dns_authorization.app[0].dns_resource_record[0].data
    }
  } : null
}

output "cloud_run_service" {
  description = "Cloud Run service name (GitHub variable GCP_CLOUD_RUN_SERVICE)."
  value       = google_cloud_run_v2_service.app.name
}

output "region" {
  description = "Region of Cloud Run and Artifact Registry (GitHub variable GCP_REGION)."
  value       = var.region
}

output "artifact_registry_repo" {
  description = "Docker repository path (GitHub variable GCP_AR_REPO)."
  value       = local.artifact_registry_repo
}

output "workload_identity_provider" {
  description = "Full WIF provider resource name (GitHub variable GCP_WIF_PROVIDER)."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "deployer_service_account" {
  description = "Service account impersonated by GitHub Actions (GitHub variable GCP_DEPLOY_SA)."
  value       = google_service_account.deployer.email
}

output "runtime_service_account" {
  description = "Service account the Cloud Run service runs as."
  value       = google_service_account.runtime.email
}

output "project_number" {
  description = "Project number (informational)."
  value       = data.google_project.current.number
}

output "api_service" {
  description = "Cloud Run service of the Marshal API (GitHub variable GCP_API_SERVICE), served at /api/* on the load balancer."
  value       = google_cloud_run_v2_service.api.name
}

output "api_service_account" {
  description = "Service account the Marshal API runs as."
  value       = google_service_account.api.email
}
