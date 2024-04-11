terraform {
  backend "gcs" {
    bucket = "gdce-cluster-provisioner-tf"
    prefix = "prod"

  }
}