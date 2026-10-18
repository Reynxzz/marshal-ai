# Marshal API (marshal_api, FastAPI; branch faris-be, .github/workflows/deploy-backend.yml) on
# Cloud Run behind the same load balancer: https://<domain>/api/* routes here (load_balancer.tf).

locals {
  api_artifacts_bucket = var.api_artifacts_bucket != "" ? var.api_artifacts_bucket : "marshal-artifacts-${var.project_id}"

  api_roles = toset([
    "roles/aiplatform.user",     # Gemini on Vertex AI
    "roles/datastore.user",      # Firestore records (MARSHAL_STORE=gcp)
    "roles/bigquery.dataEditor", # record history (MARSHAL_HISTORY=bq)
    "roles/bigquery.jobUser",
    "roles/pubsub.editor", # event bus (MARSHAL_BUS=gcp)
  ])

  # The deployed demo settings (faris-be .env.production), then var.api_env overrides.
  api_env = merge(
    {
      GOOGLE_GENAI_USE_VERTEXAI = "TRUE"
      GOOGLE_CLOUD_PROJECT      = var.project_id
      GOOGLE_CLOUD_LOCATION     = var.gemini_location
      MARSHAL_RUN_PLANT         = "1"
      MARSHAL_PLANT_SPEED       = "8"
      MARSHAL_LLM               = "gemini"
      MARSHAL_ROUTE             = "always"
      MARSHAL_STORE             = "memory"
      MARSHAL_BUS               = "memory"
      MARSHAL_BATCH             = "0"
      MARSHAL_CORS_ORIGINS      = jsonencode(local.https_enabled ? ["https://${var.domain}"] : ["http://${google_compute_global_address.lb.address}"])
      MARSHAL_BUCKET            = local.api_artifacts_bucket
      MARSHAL_MEDIA_BUCKET      = local.api_artifacts_bucket
    },
    var.api_model != "" ? { MARSHAL_MODEL = var.api_model } : {},
    var.api_env,
  )
}

# Created outside Terraform first (gcloud); create_ignore_already_exists adopts the existing
# account instead of failing, so no import is needed.
resource "google_service_account" "api" {
  account_id                   = var.api_service_account_id
  display_name                 = "Marshal AI backend"
  description                  = "marshal-ai / marshal_api runtime: Vertex Gemini, Firestore, BigQuery, Pub/Sub, artifacts bucket"
  create_ignore_already_exists = true

  depends_on = [google_project_service.required]
}

resource "google_project_iam_member" "api" {
  for_each = local.api_roles

  project = var.project_id
  role    = each.value
  member  = google_service_account.api.member
}

# Evidence photos and media (MARSHAL_ARTIFACTS=gcs, media signed URLs). The bucket itself is not
# managed here.
resource "google_storage_bucket_iam_member" "api_artifacts" {
  bucket = local.api_artifacts_bucket
  role   = "roles/storage.objectAdmin"
  member = google_service_account.api.member
}

resource "google_cloud_run_v2_service" "api" {
  name                = var.api_service_name
  location            = var.region
  deletion_protection = false
  labels              = local.labels

  # Only the load balancer may reach the service; the run.app URL returns 404 from outside.
  ingress = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"

  template {
    service_account = google_service_account.api.email
    # Long enough for the /api/v1/events SSE stream (the load balancer follows this timeout).
    timeout = "3600s"

    scaling {
      min_instance_count = var.api_min_instances
      max_instance_count = var.api_max_instances
    }

    containers {
      image = var.image

      ports {
        container_port = 8080
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
        # The in-process plant (MARSHAL_RUN_PLANT=1) runs between requests: keep CPU allocated.
        cpu_idle = false
      }

      dynamic "env" {
        for_each = local.api_env
        content {
          name  = env.key
          value = env.value
        }
      }

      startup_probe {
        initial_delay_seconds = 0
        period_seconds        = 3
        timeout_seconds       = 2
        failure_threshold     = 40
        http_get {
          path = "/api/v1/healthz"
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
    google_project_iam_member.api,
  ]
}

# Public through the load balancer, like the frontend; direct run.app access is blocked by ingress.
resource "google_cloud_run_v2_service_iam_member" "api_public" {
  project  = var.project_id
  location = google_cloud_run_v2_service.api.location
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_compute_region_network_endpoint_group" "api" {
  name                  = "${var.api_service_name}-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = google_cloud_run_v2_service.api.name
  }
}

resource "google_compute_backend_service" "api" {
  name                  = "${var.api_service_name}-backend"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTPS"
  security_policy       = google_compute_security_policy.app.id
  # No timeout_sec: serverless NEG backends do not accept one; the Cloud Run request timeout
  # (3600s above) bounds the /api/v1/events SSE stream instead.

  backend {
    group = google_compute_region_network_endpoint_group.api.id
  }

  log_config {
    enable      = true
    sample_rate = 1.0
  }
}

# The deployer (GitHub Actions) deploys revisions that run as the API service account.
resource "google_service_account_iam_member" "deployer_act_as_api" {
  service_account_id = google_service_account.api.name
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.deployer.member
}

# Signed media URLs (media_service.generate_signed_url): on Cloud Run there is no private key, so
# the API signs through the IAM signBlob API as itself. Scoped to this one service account.
resource "google_service_account_iam_member" "api_sign_self" {
  service_account_id = google_service_account.api.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = google_service_account.api.member
}
