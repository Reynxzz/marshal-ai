resource "google_artifact_registry_repository" "app" {
  location      = var.region
  repository_id = var.name_prefix
  description   = "Container images for ${var.name_prefix}, pushed by GitHub Actions."
  format        = "DOCKER"
  labels        = local.labels

  # Keep the 10 most recent images; older ones are deleted by Artifact Registry.
  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 10
    }
  }
  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      tag_state  = "ANY"
      older_than = "2592000s"
    }
  }

  depends_on = [google_project_service.required]
}

locals {
  artifact_registry_repo = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.app.repository_id}"
}
