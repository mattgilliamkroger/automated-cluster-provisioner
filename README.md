# GDCE Cluster Provisioner

This solution automatically provisions GDCE clusters as zones are turned up. This removes the need to wait and manually trigger automation after the turn up has complete. [Automated GDCE Cluster Provisioning TDD](https://docs.google.com/document/d/1nRi-V_vzmorZ7It8aPxuXnvZyih8n3wn1G73me6ACco/edit?resourcekey=0-W6AvnU-WWI1ynk4ETH0wAQ&tab=t.0#heading=h.8pa838wf1v4e)

## Pre-Requisites
### Required Roles for Terraform Agent
| GCP Role Name | Projects |
|---------------|---------|
| roles/cloudbuild.builds.editor | Main |
| roles/cloudfunctions.admin | Main |
| roles/cloudscheduler.admin | Main|
| roles/iam.serviceAccountAdmin | Main |
| roles/resourcemanager.projectIamAdmin | All |
| roles/iam.serviceAccountUser | Main
| roles/serviceusage.serviceUsageAdmin | All|
| roles/storage.admin | Main |


### Secrets
* Generate git credentials with access to target repository
* In secrets project, create a Google Secrets Manager entry and store the git credentials

## Quickstart
```
cd bootstrap

cp terraform.tfvars.example terraform.tfvars
# update the terraform.tfvars as needed

#terraform init -backend-config=env/prod.gcs.tfbackend 
terraform init -backend-config=env/staging.gcs.tfbackend   
terraform plan
terraform apply -var="environment=stg"
```

Update the `cluster-intent-registry.csv` file with new cluster intents. Trigger the manual cloud build trigger passing in the `_NODE_LOCATION` substitution to target a particular zone. 

## Providers

| Name | Version |
|------|---------|
| <a name="provider_archive"></a> [archive](#provider\_archive) | 2.4.2 |
| <a name="provider_google"></a> [google](#provider\_google) | 5.26.0 |
| <a name="provider_random"></a> [random](#provider\_random) | 3.6.1 |

## Modules

No modules.


## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_edge_container_api_endpoint_override"></a> [edge\_container\_api\_endpoint\_override](#input\_edge\_container\_api\_endpoint\_override) | Google Distributed Cloud Edge API | `string` | `"https://staging-edgecontainer.sandbox.googleapis.com/"` | no |
| <a name="input_environment"></a> [environment](#input\_environment) | Deployment environment | `string` | `"stg"` | no |
| <a name="input_gke_hub_api_endpoint_override"></a> [gke\_hub\_api\_endpoint\_override](#input\_gke\_hub\_api\_endpoint\_override) | Google Distributed Cloud Edge API | `string` | `"https://staging-gkehub.sandbox.googleapis.com/"` | no |
| <a name="input_node_location"></a> [node\_location](#input\_node\_location) | default GDCE zone used by CloudBuild | `string` | n/a | yes |
| <a name="input_project_id"></a> [project\_id](#input\_project\_id) | The Google Cloud Platform (GCP) project id in which the solution resources will be provisioned | `string` | `"cloud-alchemists-sandbox"` | no |
| <a name="input_project_id_fleet"></a> [project\_id\_fleet](#input\_project\_id\_fleet) | Optional id of GCP project hosting the Google Kubernetes Engine (GKE) fleet or Google Distributed Compute Engine (GDCE) machines. Defaults to the value of 'project\_id'. | `string` | `null` | no |
| <a name="input_project_id_secrets"></a> [project\_id\_secrets](#input\_project\_id\_secrets) | Optional id of GCP project containing the Secret Manager entry storing Git repository credentials. Defaults to the value of 'project\_id'. | `string` | `null` | no |
| <a name="input_project_services"></a> [project\_services](#input\_project\_services) | GCP Service APIs (<api>.googleapis.com) to enable for this project | `list(string)` | <pre>[<br>  "cloudbuild.googleapis.com",<br>  "cloudfunctions.googleapis.com",<br>  "cloudscheduler.googleapis.com",<br>  "run.googleapis.com",<br>  "storage.googleapis.com"<br>]</pre> | no |
| <a name="input_project_services_fleet"></a> [project\_services\_fleet](#input\_project\_services\_fleet) | GCP Service APIs (<api>.googleapis.com) to enable for this project | `list(string)` | <pre>[<br>  "anthos.googleapis.com",<br>  "anthosaudit.googleapis.com",<br>  "anthosconfigmanagement.googleapis.com",<br>  "anthosgke.googleapis.com",<br>  "artifactregistry.googleapis.com",<br>  "cloudbuild.googleapis.com",<br>  "cloudfunctions.googleapis.com",<br>  "cloudresourcemanager.googleapis.com",<br>  "cloudscheduler.googleapis.com",<br>  "connectgateway.googleapis.com",<br>  "container.googleapis.com",<br>  "edgecontainer.googleapis.com",<br>  "gkeconnect.googleapis.com",<br>  "gkehub.googleapis.com",<br>  "gkeonprem.googleapis.com",<br>  "iam.googleapis.com",<br>  "iamcredentials.googleapis.com",<br>  "logging.googleapis.com",<br>  "monitoring.googleapis.com",<br>  "opsconfigmonitoring.googleapis.com",<br>  "run.googleapis.com",<br>  "secretmanager.googleapis.com",<br>  "serviceusage.googleapis.com",<br>  "stackdriver.googleapis.com",<br>  "storage.googleapis.com",<br>  "sts.googleapis.com"<br>]</pre> | no |
| <a name="input_project_services_secrets"></a> [project\_services\_secrets](#input\_project\_services\_secrets) | GCP Service APIs (<api>.googleapis.com) to enable for this project | `list(string)` | <pre>[<br>  "secretmanager.googleapis.com"<br>]</pre> | no |
| <a name="input_region"></a> [region](#input\_region) | GCP region to deploy resources | `string` | n/a | yes |
| <a name="input_source_of_truth_repo"></a> [source_of_truth_repo](#input\_source\_of\_truth\_repo) | Repository containing source of truth cluster intent registry | `string` | n/a | yes |
| <a name="input_source_of_truth_branch"></a> [source_of_truth_branch](#input\_source\_of\_truth\_branch) | Repository branch containing source of truth cluster intent registry | `string` | n/a | yes |
| <a name="input_source_of_truth_path"></a> [source_of_truth_path](#input\_source\_of\_truth\_path) | Path to cluster intent registry file in repository | `string` | n/a | yes |
| <a name="git_secret_id"></a> [git_secret_id](#input\_git\_secret\_id) | Git token to authenticate with source of truth | `string` | n/a | yes |

## Outputs

No outputs.
