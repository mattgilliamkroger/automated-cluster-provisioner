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
  default     = [
      "anthos.googleapis.com",
      "anthosaudit.googleapis.com",
      "anthosconfigmanagement.googleapis.com",
      "anthosgke.googleapis.com",
      "artifactregistry.googleapis.com",
      "cloudbuild.googleapis.com",
      "cloudfunctions.googleapis.com",
      "cloudresourcemanager.googleapis.com",
      "cloudscheduler.googleapis.com",
      "connectgateway.googleapis.com",
      "container.googleapis.com",
      "edgecontainer.googleapis.com",
      "gkeconnect.googleapis.com",
      "gkehub.googleapis.com",
      "gkeonprem.googleapis.com",
      "iam.googleapis.com",
      "logging.googleapis.com",
      "monitoring.googleapis.com",
      "opsconfigmonitoring.googleapis.com",
      "run.googleapis.com",
      "secretmanager.googleapis.com",
      "serviceusage.googleapis.com",
      "stackdriver.googleapis.com",
      "storage.googleapis.com",
      "sts.googleapis.com",
  ]
}

variable "environment" {
  description = "Deployment environment"
  default     = "stg"
}

variable "edge_container_api_endpoint_override" {
  description = "Google Distributed Cloud Edge API"
  default     = ""
}