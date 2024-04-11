variable "project" {
  description = "GCP project name"
  default     = "cloud-alchemists-sandbox"
}

variable "region" {
  description = "GCP region to deploy resources"
  default     = "us-central1"
}

variable "gcp_project_services" {
  type        = list(any)
  description = "GCP Service APIs (<api>.googleapis.com) to enable for this project"
  default = [
    "anthos.googleapis.com",
    "anthosconfigmanagement.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudfunctions.googleapis.com",
    "cloudscheduler.googleapis.com",
    "edgecontainer.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
  ]
}

variable "environment" {
  description = "Deployment environment"
  default     = "stg"
}

variable "edge_container_api_endpoint_override" {
  description = "Google Distributed Cloud Edge API"
}