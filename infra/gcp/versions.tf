terraform {
  required_version = ">= 1.6.0, < 2.0.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }

  # State is local by default (terraform.tfstate, git-ignored). For a shared team setup, create
  # a bucket once and switch to a GCS backend, then run `terraform init -migrate-state`:
  #
  # backend "gcs" {
  #   bucket = "my-project-tfstate"
  #   prefix = "marshal-threejs/gcp"
  # }
}
