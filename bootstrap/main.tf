locals {
  cloud_build_inline_create_cluster = yamldecode(file("create-cluster.yaml"))
  cloud_build_inline_modify_cluster = yamldecode(file("modify-cluster.yaml"))
  cloud_build_substitions = merge(
    { _CLUSTER_INTENT_BUCKET = google_storage_bucket.gdce-cluster-provisioner-bucket.name},
    var.edge_container_api_endpoint_override != "" ? { _EDGE_CONTAINER_API_ENDPOINT_OVERRIDE = var.edge_container_api_endpoint_override } : {},
    var.edge_network_api_endpoint_override != "" ? { _EDGE_NETWORK_API_ENDPOINT_OVERRIDE = var.edge_network_api_endpoint_override } : {},
    var.gke_hub_api_endpoint_override != "" ? { _GKEHUB_API_ENDPOINT_OVERRIDE = var.gke_hub_api_endpoint_override } : {},
  )
}

resource "google_project_service" "project" {
  for_each = toset(var.gcp_project_services)
  service  = each.value

  disable_on_destroy = false
}

resource "google_storage_bucket" "gdce-cluster-provisioner-bucket" {
  name          = "gdce-cluster-provisioner-bucket-${var.environment}"
  location      = "US"
  storage_class = "STANDARD"

  uniform_bucket_level_access = true
}

resource "google_storage_bucket_object" "apply-spec" {
  name         = "apply-spec.yaml.template"
  source       = "./apply-spec.yaml.template"
  content_type = "text/plain"
  bucket       = google_storage_bucket.gdce-cluster-provisioner-bucket.id
}

resource "google_storage_bucket_object" "cluster-secret-store" {
  name         = "cluster-secret-store.yaml.template"
  source       = "./cluster-secret-store.yaml.template"
  content_type = "text/plain"
  bucket       = google_storage_bucket.gdce-cluster-provisioner-bucket.id
}

resource "google_storage_bucket_object" "cluster-intent-registry" {
  name         = "cluster-intent-registry.csv"
  source       = "./cluster-intent-registry.csv"
  content_type = "text/plain"
  bucket       = google_storage_bucket.gdce-cluster-provisioner-bucket.id
}

resource "google_cloudbuild_trigger" "create-cluster" {
  location        = var.region
  name            = "gdce-cluster-provisioner-trigger-${var.environment}"
  service_account = "projects/${var.project}/serviceAccounts/${google_service_account.gdce-provisioning-agent.email}"
  substitutions = local.cloud_build_substitions

  build {
    substitutions = local.cloud_build_substitions
    timeout       = try(local.cloud_build_inline_create_cluster["timeout"], "14400s")

    options {
      logging = try(local.cloud_build_inline_create_cluster["options"]["logging"], null)
    }

    dynamic "step" {
      for_each = try(local.cloud_build_inline_create_cluster["steps"], [])
      content {
        env    = try(step.value.env, [])
        id     = try(step.value.id, null)
        name   = try(step.value.name, null)
        script = try(step.value.script, null)
      }
    }
  }

  # workaround to create manual trigger: https://github.com/hashicorp/terraform-provider-google/issues/16295
  webhook_config {
    secret = ""
  }
  lifecycle {
    ignore_changes = [webhook_config]
  }
}

resource "google_cloudbuild_trigger" "modify-cluster" {
  location        = var.region
  name            = "gdce-cluster-reconciler-trigger-${var.environment}"
  service_account = "projects/${var.project}/serviceAccounts/${google_service_account.gdce-provisioning-agent.email}"
  substitutions = local.cloud_build_substitions

  build {
    substitutions = local.cloud_build_substitions
    timeout       = try(local.cloud_build_inline_modify_cluster["timeout"], "14400s")

    options {
      logging = try(local.cloud_build_inline_modify_cluster["options"]["logging"], null)
    }

    dynamic "step" {
      for_each = try(local.cloud_build_inline_modify_cluster["steps"], [])
      content {
        env    = try(step.value.env, [])
        id     = try(step.value.id, null)
        name   = try(step.value.name, null)
        script = try(step.value.script, null)
      }
    }
  }

  # workaround to create manual trigger: https://github.com/hashicorp/terraform-provider-google/issues/16295
  webhook_config {
    secret = ""
  }
  lifecycle {
    ignore_changes = [webhook_config]
  }
}

data "archive_file" "modify-cluster" {
  type        = "zip"
  output_path = "/tmp/modify_cluster.zip"
  source_file = "./modify-cluster.yaml"
}

resource "google_storage_bucket_object" "modify-cluster-src" {
  name   = "modify_cluster.zip"
  bucket = google_storage_bucket.gdce-cluster-provisioner-bucket.name
  source = data.archive_file.modify-cluster.output_path
}

resource "google_service_account" "gdce-provisioning-agent" {
  account_id = "gdce-prov-agent-${var.environment}"
}

resource "google_project_iam_member" "gdce-provisioning-agent-edge-admin" {
  project = var.project
  role    = "roles/edgecontainer.admin"
  member  = google_service_account.gdce-provisioning-agent.member
}

resource "google_project_iam_member" "gdce-provisioning-agent-storage-admin" {
  project = var.project
  role    = "roles/storage.admin"
  member  = google_service_account.gdce-provisioning-agent.member
}

resource "google_project_iam_member" "gdce-provisioning-agent-log-writer" {
  project = var.project
  role    = "roles/logging.logWriter"
  member  = google_service_account.gdce-provisioning-agent.member
}

resource "google_project_iam_member" "gdce-provisioning-agent-secret-accessor" {
  project = var.project
  role    = "roles/secretmanager.secretAccessor"
  member  = google_service_account.gdce-provisioning-agent.member
}

resource "google_project_iam_member" "gdce-provisioning-agent-hub-admin" {
  project = var.project
  role    = "roles/gkehub.admin"
  member  = google_service_account.gdce-provisioning-agent.member
}

resource "google_project_iam_member" "gdce-provisioning-agent-hub-gateway" {
  project = var.project
  role    = "roles/gkehub.gatewayAdmin"
  member  = google_service_account.gdce-provisioning-agent.member
}

resource "google_service_account" "es-agent" {
  account_id = "es-agent-${var.environment}"
}

resource "google_project_iam_member" "es-agent-secret-accessor" {
  project = var.project
  role    = "roles/secretmanager.secretAccessor"
  member  = google_service_account.es-agent.member
}

data "archive_file" "zone-watcher" {
  type        = "zip"
  output_path = "/tmp/zone_watcher_gcf.zip"
  source_dir  = "../zone-watcher/cloud_function_source/"
}

resource "google_storage_bucket_object" "zone-watcher-src" {
  name   = "zone_watcher_gcf.zip"
  bucket = google_storage_bucket.gdce-cluster-provisioner-bucket.name
  source = data.archive_file.zone-watcher.output_path # Add path to the zipped function source code
}

data "archive_file" "cluster-watcher" {
  type        = "zip"
  output_path = "/tmp/cluster_watcher_gcf.zip"
  source_dir  = "../cluster-watcher/cloud_function_source/"
}

resource "google_storage_bucket_object" "cluster-watcher-src" {
  name   = "cluster_watcher_gcf.zip"
  bucket = google_storage_bucket.gdce-cluster-provisioner-bucket.name
  source = data.archive_file.cluster-watcher.output_path # Add path to the zipped function source code
}

resource "google_service_account" "zone-watcher-agent" {
  account_id   = "zone-watcher-agent-${var.environment}"
  display_name = "Zone Watcher Service Account"
}

resource "google_project_iam_member" "zone-watcher-agent-storage-admin" {
  project = var.project
  role    = "roles/storage.admin"
  member  = google_service_account.zone-watcher-agent.member
}

resource "google_project_iam_member" "zone-watcher-agent-cloud-build-editor" {
  project = var.project
  role    = "roles/cloudbuild.builds.editor"
  member  = google_service_account.zone-watcher-agent.member
}

resource "google_project_iam_member" "zone-watcher-agent-impersonate-sa" {
  project = var.project
  role    = "roles/iam.serviceAccountTokenCreator"
  member  = google_service_account.zone-watcher-agent.member
}

resource "google_project_iam_member" "zone-watcher-agent-token-user" {
  project = var.project
  role    = "roles/iam.serviceAccountUser"
  member  = google_service_account.zone-watcher-agent.member
}

resource "google_project_iam_member" "zone-watcher-agent-edge-viewer" {
  project = var.project
  role    = "roles/edgecontainer.viewer"
  member  = google_service_account.zone-watcher-agent.member
}

# zone-watcher cloud function
resource "google_cloudfunctions2_function" "zone-watcher" {
  name        = "zone-watcher-${var.environment}"
  location    = var.region
  description = "zone watcher function"

  build_config {
    runtime     = "python312"
    entry_point = "zone_watcher"
    environment_variables = {
      "SOURCE_SHA" = data.archive_file.zone-watcher.output_sha # https://github.com/hashicorp/terraform-provider-google/issues/1938
    }
    source {
      storage_source {
        bucket = google_storage_bucket.gdce-cluster-provisioner-bucket.name
        object = google_storage_bucket_object.zone-watcher-src.name
      }
    }
  }

  service_config {
    max_instance_count = 1
    available_memory   = "256M"
    timeout_seconds    = 60
    environment_variables = {
      GOOGLE_CLOUD_PROJECT                 = var.project,
      CONFIG_CSV                           = "gs://${google_storage_bucket.gdce-cluster-provisioner-bucket.name}/${google_storage_bucket_object.cluster-intent-registry.output_name}",
      CB_TRIGGER_NAME                      = "gdce-cluster-provisioner-trigger-${var.environment}"
      REGION                               = var.region
      EDGE_CONTAINER_API_ENDPOINT_OVERRIDE = var.edge_container_api_endpoint_override
    }
    service_account_email = google_service_account.zone-watcher-agent.email
  }
}

resource "google_cloud_run_service_iam_member" "member" {
  location = google_cloudfunctions2_function.zone-watcher.location
  service  = google_cloudfunctions2_function.zone-watcher.name
  role     = "roles/run.invoker"
  member   = google_service_account.gdce-provisioning-agent.member
}

resource "google_cloud_scheduler_job" "job" {
  name             = "zone-watcher-scheduler-${var.environment}"
  description      = "Trigger the ${google_cloudfunctions2_function.zone-watcher.name}"
  schedule         = "0 * * * *" # Run every hour
  time_zone        = "Europe/Dublin"
  attempt_deadline = "320s"
  region           = var.region

  http_target {
    http_method = "POST"
    uri         = google_cloudfunctions2_function.zone-watcher.service_config[0].uri

    oidc_token {
      service_account_email = google_service_account.gdce-provisioning-agent.email
    }
  }
}

# Cluster Watcher cloud function
resource "google_cloudfunctions2_function" "cluster-watcher" {
  name        = "cluster-watcher-${var.environment}"
  location    = var.region
  description = "cluster watcher function"

  build_config {
    runtime     = "python312"
    entry_point = "cluster_watcher"
    environment_variables = {
      "SOURCE_SHA" = data.archive_file.cluster-watcher.output_sha # https://github.com/hashicorp/terraform-provider-google/issues/1938
    }
    source {
      storage_source {
        bucket = google_storage_bucket.gdce-cluster-provisioner-bucket.name
        object = google_storage_bucket_object.cluster-watcher-src.name
      }
    }
  }

  service_config {
    max_instance_count = 1
    available_memory   = "256M"
    timeout_seconds    = 60
    environment_variables = {
      GOOGLE_CLOUD_PROJECT                 = var.project,
      CONFIG_CSV                           = "gs://${google_storage_bucket.gdce-cluster-provisioner-bucket.name}/${google_storage_bucket_object.cluster-intent-registry.output_name}",
      CB_TRIGGER_NAME                      = "gdce-cluster-reconciler-trigger-${var.environment}"
      REGION                               = var.region
      EDGE_CONTAINER_API_ENDPOINT_OVERRIDE = var.edge_container_api_endpoint_override
      EDGE_NETWORK_API_ENDPOINT_OVERRIDE = var.edge_network_api_endpoint_override
    }
    service_account_email = google_service_account.zone-watcher-agent.email
  }
}

resource "google_cloud_run_service_iam_member" "cluster-watcher-member" {
  location = google_cloudfunctions2_function.cluster-watcher.location
  service  = google_cloudfunctions2_function.cluster-watcher.name
  role     = "roles/run.invoker"
  member   = google_service_account.gdce-provisioning-agent.member
}

resource "google_cloud_scheduler_job" "cluster-watcher-job" {
  name             = "cluster-watcher-scheduler-${var.environment}"
  description      = "Trigger the ${google_cloudfunctions2_function.cluster-watcher.name}"
  schedule         = "0 */2 * * *" # Run every 2 hours
  time_zone        = "Europe/Dublin"
  attempt_deadline = "320s"
  region           = var.region

  http_target {
    http_method = "POST"
    uri         = google_cloudfunctions2_function.cluster-watcher.service_config[0].uri

    oidc_token {
      service_account_email = google_service_account.gdce-provisioning-agent.email
    }
  }
}