variable "store_id" {
  description = "Store ID, used to lookup zone info"
  type        = string
  default     = null
}

variable "zone" {
  description = "Zone name"
  type        = string
  default     = null
}

variable "project_id" {
  description = "The Google Cloud Platform (GCP) project id in which the solution resources will be provisioned"
  type        = string
}

variable "project_id_fleet" {
  description = "Optional id of GCP project hosting the Google Kubernetes Engine (GKE) fleet or Google Distributed Compute Engine (GDCE) machines. Defaults to the value of 'project_id'."
  default     = null
  type        = string
}

variable "project_id_secrets" {
  description = "Optional id of GCP project containing the Secret Manager entry storing Git repository credentials. Defaults to the value of 'project_id'."
  default     = null
  type        = string
}

variable "region" {
  description = "GCP region to deploy resources"
  type        = string
}

variable "project_services" {
  type        = list(string)
  description = "GCP Service APIs (<api>.googleapis.com) to enable for this project"
  default = [
    "cloudbuild.googleapis.com",
    "cloudfunctions.googleapis.com",
    "cloudscheduler.googleapis.com",
    "run.googleapis.com",
    "storage.googleapis.com",
  ]
}

# prune list of required services later
variable "project_services_fleet" {
  type        = list(string)
  description = "GCP Service APIs (<api>.googleapis.com) to enable for this project"
  default = [
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
    "iamcredentials.googleapis.com",
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

variable "project_services_secrets" {
  type        = list(string)
  description = "GCP Service APIs (<api>.googleapis.com) to enable for this project"
  default = [
    "secretmanager.googleapis.com",
  ]
}

variable "environment" {
  description = "Deployment environment"
  type        = string
}

variable "edge_container_api_endpoint_override" {
  description = "Google Distributed Cloud Edge API"
  default     = ""
}

variable "edge_network_api_endpoint_override" {
  description = "Google Distributed Cloud Edge Network API"
  default     = ""
}

variable "gke_hub_api_endpoint_override" {
  description = "Google Distributed Cloud Edge API"
  default     = ""
}

variable "source_of_truth_repo" {
  description = "Source of truth repository"
  default = "gitlab.com/gcp-solutions-public/retail-edge/gdce-shyguy-internal/cluster-intent-registry"
}

variable "source_of_truth_branch" {
  description = "Source of truth branch"
  default = "main"
}

variable "source_of_truth_path" {
  description = "Path to cluster intent registry file"
  default = "source_of_truth.csv"
}

variable "git_secret_id" {
  description = "Secrets manager secret holding git token to pull source of truth"
  default = "shyguy-internal-pat"
}