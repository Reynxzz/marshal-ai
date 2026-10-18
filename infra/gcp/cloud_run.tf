resource "google_service_account" "runtime" {
  account_id   = "${var.name_prefix}-run"
  display_name = "${var.name_prefix} Cloud Run runtime"
  description  = "Identity of the Cloud Run service. Calls Vertex AI Gemini."

  depends_on = [google_project_service.required]
}

resource "google_project_iam_member" "runtime_vertex" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = google_service_account.runtime.member
}

resource "google_cloud_run_v2_service" "app" {
  name                = var.name_prefix
  location            = var.region
  deletion_protection = false
  labels              = local.labels

  # Only the load balancer may reach the service; the run.app URL returns 404 from outside.
  ingress = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"

  template {
    service_account = google_service_account.runtime.email

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    containers {
      image = var.image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
        cpu_idle = true
      }

      env {
        name  = "GOOGLE_GENAI_USE_VERTEXAI"
        value = "true"
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.gemini_location
      }
      env {
        name  = "GEMINI_MODEL"
        value = var.gemini_model
      }

      startup_probe {
        initial_delay_seconds = 0
        period_seconds        = 3
        timeout_seconds       = 2
        failure_threshold     = 10
        http_get {
          path = "/healthz"
          port = 8080
        }
      }
    }
  }

  lifecycle {
    # GitHub Actions deploys new images (and adds its labels); do not let Terraform revert them.
    ignore_changes = [
      template[0].containers[0].image,
      template[0].labels,
      labels,
      client,
      client_version,
    ]
  }

  depends_on = [
    google_project_service.required,
    google_project_iam_member.runtime_vertex,
  ]
}

# The app is public: anyone reaching the load balancer may invoke it. Direct run.app access is
# still blocked by the ingress setting above.
resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = var.project_id
  location = google_cloud_run_v2_service.app.location
  name     = google_cloud_run_v2_service.app.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
