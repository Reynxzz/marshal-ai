# Workload Identity Federation: GitHub Actions in <github_repository> can impersonate the deployer
# service account with short-lived tokens (no JSON keys), only from the (branch, workflow file)
# pairs in github_deploy_workflows.

locals {
  deploy_refs = [for b in keys(var.github_deploy_workflows) : "refs/heads/${b}"]
  # A workflow_ref names both the file and the ref it runs from, so each entry is one exact pair.
  deploy_workflow_refs = [
    for b, f in var.github_deploy_workflows : "${var.github_repository}/.github/workflows/${f}@refs/heads/${b}"
  ]
}

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "${var.name_prefix}-gh"
  display_name              = "GitHub Actions"
  description               = "OIDC tokens from GitHub Actions for ${var.github_repository}."

  depends_on = [google_project_service.required]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"
  description                        = "Accepts ${var.github_repository} on ${join(", ", local.deploy_refs)} only."

  attribute_mapping = {
    "google.subject"          = "assertion.sub"
    "attribute.repository"    = "assertion.repository"
    "attribute.repository_id" = "assertion.repository_id"
    "attribute.ref"           = "assertion.ref"
    "attribute.actor"         = "assertion.actor"
    "attribute.workflow_ref"  = "assertion.workflow_ref"
  }

  # Only a deploy branch running its own deploy workflow file can authenticate: another workflow
  # merged into the branch (or the other branch's workflow) cannot impersonate the deployer. The
  # numeric repository id (when set) also survives renames and blocks a re-created repo with the same name.
  attribute_condition = join(" && ", compact([
    "assertion.repository == '${var.github_repository}'",
    "assertion.ref in [${join(", ", [for r in local.deploy_refs : "'${r}'"])}]",
    "assertion.workflow_ref in [${join(", ", [for r in local.deploy_workflow_refs : "'${r}'"])}]",
    var.github_repository_id != "" ? "assertion.repository_id == '${var.github_repository_id}'" : "",
  ]))

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "deployer" {
  account_id   = "${var.name_prefix}-deploy"
  display_name = "${var.name_prefix} GitHub Actions deployer"
  description  = "Pushes images to Artifact Registry and deploys Cloud Run revisions."

  depends_on = [google_project_service.required]
}

resource "google_service_account_iam_member" "deployer_wif" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}

resource "google_project_iam_member" "deployer_run" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = google_service_account.deployer.member
}

resource "google_artifact_registry_repository_iam_member" "deployer_push" {
  project    = var.project_id
  location   = google_artifact_registry_repository.app.location
  repository = google_artifact_registry_repository.app.name
  role       = "roles/artifactregistry.writer"
  member     = google_service_account.deployer.member
}

# Deploying a revision that runs as the runtime SA requires actAs on that SA only.
resource "google_service_account_iam_member" "deployer_act_as_runtime" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = google_service_account.deployer.member
}
