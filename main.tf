resource "google_storage_bucket" "gdce-cluster-provisioner-bucket" {
    name = "gdce-cluster-provisioner-bucket"
    location = "US"
    storage_class = "STANDARD"

    uniform_bucket_level_access = true
}

resource "google_storage_bucket_object" "apply-spec" {
    name = "apply-spec.yaml.template"
    source = "./apply-spec.yaml.template"
    content_type = "text/plain"
    bucket = google_storage_bucket.gdce-cluster-provisioner-bucket.id
}

resource "google_storage_bucket_object" "cluster-secret-store" {
    name = "cluster-secret-store.yaml.template"
    source = "./cluster-secret-store.yaml"
    content_type = "text/plain"
    bucket = google_storage_bucket.gdce-cluster-provisioner-bucket.id
}

resource "google_storage_bucket_object" "cluster-intent-registry" {
    name = "cluster-intent-registry.csv"
    source = "./cluster-intent-registry.csv"
    content_type = "text/plain"
    bucket = google_storage_bucket.gdce-cluster-provisioner-bucket.id
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
       --name=gdce-cluster-provisioner-trigger \
       --inline-config=create-cluster.yaml \
       --region=us-central1 \
       --service-account=projects/gmec-developers-1/serviceAccounts/gdce-provisioning-agent@gmec-developers-1.iam.gserviceaccount.com
   EOL
  destroy_cmd_entrypoint = "gcloud"
  destroy_cmd_body       = "alpha builds triggers delete gdce-cluster-provisioner-trigger --region us-central1"
}

resource "google_service_account" "gdce-provisioning-agent" {
    account_id = "gdce-provisioning-agent"
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