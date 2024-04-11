resource "google_project_service" "project" {
  for_each = toset(var.gcp_project_services)
  service  = each.value

  disable_on_destroy = false
}

resource "google_storage_bucket" "gdce-cluster-provisioner-bucket-prod" {
    name = "gdce-cluster-provisioner-bucket-prod"
    location = "US"
    storage_class = "STANDARD"

    uniform_bucket_level_access = true
}

resource "google_storage_bucket_object" "apply-spec" {
    name = "apply-spec.yaml.template"
    source = "./apply-spec.yaml.template"
    content_type = "text/plain"
    bucket = google_storage_bucket.gdce-cluster-provisioner-bucket-prod.id
}

resource "google_storage_bucket_object" "cluster-secret-store" {
    name = "cluster-secret-store.yaml.template"
    source = "./cluster-secret-store.yaml"
    content_type = "text/plain"
    bucket = google_storage_bucket.gdce-cluster-provisioner-bucket-prod.id
}

resource "google_storage_bucket_object" "cluster-intent-registry" {
    name = "cluster-intent-registry.csv"
    source = "./cluster-intent-registry.csv"
    content_type = "text/plain"
    bucket = google_storage_bucket.gdce-cluster-provisioner-bucket-prod.id
}


// Not using google_cloudbuild_trigger resource due to missing 
// `automapSubstitutions` options and inline-config
module "gcloud" {
  source  = "terraform-google-modules/gcloud/google"
  version = "~> 3.4"

  platform = "linux"
  additional_components = ["alpha"]

  create_cmd_entrypoint  = "gcloud"
  create_cmd_body        = <<EOL
     alpha builds triggers create manual \
       --name=gdce-cluster-provisioner-trigger-prod \
       --inline-config=create-cluster.yaml \
       --region=${var.region} \
       --service-account=projects/${var.project}/serviceAccounts/gdce-provisioning-agent-prod@${var.project}.iam.gserviceaccount.com
   EOL
  destroy_cmd_entrypoint = "gcloud"
  destroy_cmd_body       = "alpha builds triggers delete gdce-cluster-provisioner-trigger-prod --region ${var.region}"
}

resource "google_service_account" "gdce-provisioning-agent-prod" {
    account_id = "gdce-provisioning-agent-prod"
}

resource "google_project_iam_member" "gdce-provisioning-agent-prod-edge-admin" {
  project = var.project
  role    = "roles/edgecontainer.admin"
  member  = google_service_account.gdce-provisioning-agent-prod.member
}

resource "google_project_iam_member" "gdce-provisioning-agent-prod-storage-admin" {
  project = var.project
  role    = "roles/storage.admin"
  member  = google_service_account.gdce-provisioning-agent-prod.member
}

resource "google_project_iam_member" "gdce-provisioning-agent-prod-log-writer" {
  project = var.project
  role    = "roles/logging.logWriter"
  member  = google_service_account.gdce-provisioning-agent-prod.member
}

resource "google_project_iam_member" "gdce-provisioning-agent-prod-secret-accessor" {
  project = var.project
  role    = "roles/secretmanager.secretAccessor"
  member  = google_service_account.gdce-provisioning-agent-prod.member
}

resource "google_project_iam_member" "gdce-provisioning-agent-prod-hub-admin" {
  project = var.project
  role    = "roles/gkehub.admin"
  member  = google_service_account.gdce-provisioning-agent-prod.member
}

resource "google_project_iam_member" "gdce-provisioning-agent-prod-hub-gateway" {
  project = var.project
  role    = "roles/gkehub.gatewayAdmin"
  member  = google_service_account.gdce-provisioning-agent-prod.member
}

resource "google_service_account" "es-agent-prod" {
    account_id = "es-agent-prod"
}

resource "google_project_iam_member" "es-agent-prod-secret-accessor" {
  project = var.project
  role    = "roles/secretmanager.secretAccessor"
  member  = google_service_account.es-agent-prod.member
}

data "archive_file" "zone-watcher-prod" {
  type        = "zip"
  output_path = "/tmp/zone_watcher_gcf.zip"
  source_dir  = "../zone-watcher/cloud_function_source/"
}

resource "google_storage_bucket_object" "zone-watcher-prod-src" {
  name   = "zone_watcher_gcf.zip"
  bucket = google_storage_bucket.gdce-cluster-provisioner-bucket-prod.name
  source = data.archive_file.zone-watcher-prod.output_path # Add path to the zipped function source code
}

resource "google_service_account" "zone-watcher-prod-agent" {
  account_id = "zone-watcher-prod-agent"
  display_name = "Zone Watcher Service Account"
}

resource "google_project_iam_member" "zone-watcher-prod-agent-storage-admin" {
  project = var.project
  role    = "roles/storage.admin"
  member  = google_service_account.zone-watcher-prod-agent.member
}

resource "google_project_iam_member" "zone-watcher-prod-agent-cloud-build-editor" {
  project = var.project
  role    = "roles/cloudbuild.builds.editor"
  member  = google_service_account.zone-watcher-prod-agent.member
}

resource "google_project_iam_member" "zone-watcher-prod-agent-impersonate-sa" {
  project = var.project
  role    = "roles/iam.serviceAccountTokenCreator"
  member  = google_service_account.zone-watcher-prod-agent.member
}

resource "google_project_iam_member" "zone-watcher-prod-agent-token-user" {
  project = var.project
  role    = "roles/iam.serviceAccountUser"
  member  = google_service_account.zone-watcher-prod-agent.member
}

# zone-watcher cloud function
resource "google_cloudfunctions2_function" "zone-watcher-prod" {
  name        = "zone-watcher-prod"
  location    = var.region
  description = "zone watcher function"

  build_config {
    runtime     = "python312"
    entry_point = "zone_watcher" # Set the entry point
    source {
      storage_source {
        bucket = google_storage_bucket.gdce-cluster-provisioner-bucket-prod.name
        object = google_storage_bucket_object.zone-watcher-prod-src.name
      }
    }
  }

  service_config {
    max_instance_count = 1
    available_memory   = "256M"
    timeout_seconds    = 60
    environment_variables = {
      GOOGLE_CLOUD_PROJECT = var.project,
      CONFIG_CSV = "gs://${google_storage_bucket.gdce-cluster-provisioner-bucket-prod.name}/${google_storage_bucket_object.cluster-intent-registry.output_name}",
      CB_TRIGGER_NAME = "gdce-cluster-provisioner-trigger-prod"
      REGION = var.region
    }
    service_account_email = google_service_account.zone-watcher-prod-agent.email
  }
}


resource "google_cloud_run_service_iam_member" "member" {
  location = google_cloudfunctions2_function.zone-watcher-prod.location
  service  = google_cloudfunctions2_function.zone-watcher-prod.name
  role     = "roles/run.invoker"
  member   = google_service_account.gdce-provisioning-agent-prod.member
}

resource "google_cloud_scheduler_job" "job" {
  name             = "zone-watcher-prod-scheduler"
  description      = "Trigger the ${google_cloudfunctions2_function.zone-watcher-prod.name}"
  schedule         = "0 0 1 * *"  # TBC
  time_zone        = "Europe/Dublin"  # TBC
  attempt_deadline = "320s"  # TBC
  region = var.region

  http_target {
    http_method = "POST"
    uri         = google_cloudfunctions2_function.zone-watcher-prod.service_config[0].uri

    oidc_token {
      service_account_email = google_service_account.gdce-provisioning-agent-prod.email
    }
  }
}