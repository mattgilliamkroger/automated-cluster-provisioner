from dataclasses import dataclass
import functions_framework
import sys
import os
import io
import flask
import csv
import logging
import requests
import google_crc32c
from requests.structures import CaseInsensitiveDict
from urllib.parse import urlparse
from google.api_core import client_options
from google.cloud import edgecontainer
from google.cloud import edgenetwork
from google.cloud import secretmanager
from google.cloud import storage
from google.cloud.devtools import cloudbuild

logger = logging.getLogger()
logging.basicConfig(stream=sys.stdout, level=os.environ.get("LOG_LEVEL", "INFO").upper())

@dataclass
class WatcherParameters:
    project_id: str
    secrets_project_id: str
    region: str
    git_secret_id: str
    source_of_truth_repo: str
    source_of_truth_branch: str
    source_of_truth_path: str
    cloud_build_trigger: str

def get_parameters_from_environment():
    proj_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    region = os.environ.get("REGION")
    secrets_project = os.environ.get("PROJECT_ID_SECRETS")
    git_secret_id = os.environ.get("GIT_SECRET_ID")
    source_of_truth_repo = os.environ.get("SOURCE_OF_TRUTH_REPO")
    source_of_truth_branch = os.environ.get("SOURCE_OF_TRUTH_BRANCH")
    source_of_truth_path = os.environ.get("SOURCE_OF_TRUTH_PATH")

    cb_trigger = f'projects/{proj_id}/locations/{region}/triggers/{os.environ.get("CB_TRIGGER_NAME")}'

    if secrets_project is None:
        secrets_project = proj_id

    if proj_id is None:
        raise Exception('missing GOOGLE_CLOUD_PROJECT, (gcs csv file project)')
    if region is None:
        raise Exception('missing REGION (us-central1)')
    if cb_trigger is None:
        raise Exception('missing CB_TRIGGER_NAME (projects/<project-id>/locations/<location>/triggers/<trigger-name>)')
    if git_secret_id is None:
        raise Exception('missing secret id for git pull credentials')
    if source_of_truth_repo is None:
        raise Exception('missing source of truth repository')
    if source_of_truth_branch is None:
        raise Exception('missing source of truth branch')
    if source_of_truth_path is None:
        raise Exception('missing path and name of source of truth')
    if '//' in source_of_truth_repo:
        raise Exception('provide repo in the form of (github.com/org_name/repo_name) or (gitlab.com/org_name/repo_name)')
    
    return WatcherParameters(
        project_id=proj_id,
        secrets_project_id=secrets_project,
        region=region,
        cloud_build_trigger=cb_trigger,
        git_secret_id=git_secret_id,
        source_of_truth_repo=source_of_truth_repo,
        source_of_truth_branch=source_of_truth_branch,
        source_of_truth_path=source_of_truth_path
    )
 
@functions_framework.http
def zone_watcher(req: flask.Request):
    params = get_parameters_from_environment()

    logger.info(f'Running zone watcher for: proj_id={params.project_id},sot={params.source_of_truth_repo}/{params.source_of_truth_branch}/{params.source_of_truth_path}, cb_trigger={params.cloud_build_trigger}')

    # Get the CSV file from GCS containing target zones
    # NODE_LOCATION	MACHINE_PROJECT_ID	FLEET_PROJECT_ID	CLUSTER_NAME	LOCATION	NODE_COUNT	EXTERNAL_LOAD_BALANCER_IPV4_ADDRESS_POOLS	SYNC_REPO	SYNC_BRANCH	SYNC_DIR	GIT_TOKEN_SECRETS_MANAGER_NAME
    # us-central1-edge-den25349	cloud-alchemist-machines	gmec-developers-1	lcp-den29	us-central1	1	172.17.34.96-172.17.34.100	https://gitlab.com/gcp-solutions-public/retail-edge/gdce-shyguy-internal/primary-root-repo	main	/config/clusters/den29/meta	shyguy-internal-pat
    config_zone_info = {}
    token = get_git_token_from_secrets_manager(params.secrets_project_id, params.git_secret_id)
    intent_reader = ClusterIntentReader(params.source_of_truth_repo, params.source_of_truth_branch, params.source_of_truth_path, token)
    zone_config_fio = intent_reader.retrieve_source_of_truth()
    rdr = csv.DictReader(io.StringIO(zone_config_fio))  # will raise exception if csv parsing fails
    machine_proj_loc = set()
    for row in rdr:
        if row['LOCATION'] not in config_zone_info.keys():
            config_zone_info[row['LOCATION']] = {}
        config_zone_info[row['LOCATION']][row['NODE_LOCATION']] = row
        machine_proj_loc.add((row['MACHINE_PROJECT_ID'], row['LOCATION']))
    for loc in config_zone_info:
        logger.debug(f'Zones to check in {loc} => {len(config_zone_info[loc])}')
    if len(config_zone_info) == 0:
        raise Exception('no valid zone listed in config file')

    edgecontainer_api_endpoint_override = os.environ.get("EDGE_CONTAINER_API_ENDPOINT_OVERRIDE")

    if edgecontainer_api_endpoint_override is not None and edgecontainer_api_endpoint_override != "":
        op = client_options.ClientOptions(api_endpoint=urlparse(edgecontainer_api_endpoint_override).netloc)
        ec_client = edgecontainer.EdgeContainerClient(client_options=op)
    else:  # use the default prod endpoint
        ec_client = edgecontainer.EdgeContainerClient()

    cb_client = cloudbuild.CloudBuildClient()

    # get machines list per machine_project per location, and group by GDCE zone
    machine_lists = {}
    for m_proj, loc in machine_proj_loc:
        req = edgecontainer.ListMachinesRequest(
            parent=ec_client.common_location_path(m_proj, loc)
        )
        res_pager = ec_client.list_machines(req)
        for m in res_pager:
            if m.zone not in machine_lists:
                machine_lists[m.zone] = [m]
            else:
                machine_lists[m.zone].append(m)

    # if cluster already present in the zone, skip this zone
    # method: check all the machines in the zone, and check if "hosted_node" has any value in it
    count = 0
    for loc in config_zone_info:
        for z in config_zone_info[loc]:
            if z not in machine_lists:
                logger.warning(f'No machine found in {z}')
                continue
            has_cluster = False
            for m in machine_lists[z]:
                if len(m.hosted_node.strip()) > 0:  # if there is any value, consider there is a cluster
                    logger.info(f'ZONE {z}: {m.name} already used by {m.hosted_node}')
                    has_cluster = True
                    break
            if has_cluster:
                continue
            # trigger cloudbuild to initiate the cluster building
            repo_source = cloudbuild.RepoSource()
            repo_source.branch_name = config_zone_info[loc][z]['SYNC_BRANCH']
            repo_source.substitutions = {
                "_NODE_LOCATION": z
            }
            req = cloudbuild.RunBuildTriggerRequest(
                name=params.cloud_build_trigger,
                source=repo_source
            )
            logger.debug(req)
            try:
                logger.info(f'triggering cloud build for {z}')
                logger.info(f'trigger: {params.cloud_build_trigger}')
                opr = cb_client.run_build_trigger(request=req)
                # response = opr.result()
            except Exception as err:
                logger.error(err)

            count += len(config_zone_info[loc])

    logger.info(f'total zones triggered = {count}')

    return f'total zones triggered = {count}'

@functions_framework.http
def cluster_watcher(req: flask.Request):
    params = get_parameters_from_environment()

    logger.info(f'proj_id = {params.project_id}')
    logger.info(f'cb_trigger = {params.cloud_build_trigger}')

    # Get the CSV file from GCS containing target zones
    # "NODE_LOCATION",              "MACHINE_PROJECT_ID",           "FLEET_PROJECT_ID",         "CLUSTER_NAME", "LOCATION", "NODE_COUNT",   "EXTERNAL_LOAD_BALANCER_IPV4_ADDRESS_POOLS","SYNC_REPO",                                                                                "SYNC_BRANCH",  "SYNC_DIR",                     "GIT_TOKEN_SECRETS_MANAGER_NAME",   "ES_AGENT_SECRETS_MANAGER_NAME", "SUBNET_VLANS",    "SUBNET_IPV4_ADDRESSES",    "MAINTENANCE_WINDOW_START", "MAINTENANCE_WINDOW_END",   "MAINTENANCE_WINDOW_RECURRENCE"
    # "us-central1-edge-den25349",  "cloud-alchemists-machines",    "cloud-alchemists-sandbox", "lcp-den29",    "us-central1",  "1",        "172.17.34.96-172.17.34.100",               "https://gitlab.com/gcp-solutions-public/retail-edge/gdce-shyguy-internal/primary-root-repo","main",        "/config/clusters/den29/meta",  "shyguy-internal-pat",              "external-secret-agent-key"
    # "us-central1-edge-den59566",  "edgesites-baremetal-lab-qual", "cloud-alchemists-sandbox", "lcp-den84",    "us-central1",  "3",        "172.20.4.240-172.20.4.248",                "https://gitlab.com/gcp-solutions-public/retail-edge/gdce-shyguy-internal/primary-root-repo","main",        "/config/clusters/den84/meta",  "shyguy-internal-pat",              "external-secret-agent-key"
    config_zone_info = {}
    token = get_git_token_from_secrets_manager(params.secrets_project_id, params.git_secret_id)
    intent_reader = ClusterIntentReader(params.source_of_truth_repo, params.source_of_truth_branch, params.source_of_truth_path, token)
    zone_config_fio = intent_reader.retrieve_source_of_truth()
    rdr = csv.DictReader(io.StringIO(zone_config_fio))  # will raise exception if csv parsing fails
    for row in rdr:
        if row['LOCATION'] not in config_zone_info.keys():
            config_zone_info[row['LOCATION']] = {}
        config_zone_info[row['LOCATION']][row['NODE_LOCATION']] = row
    for loc in config_zone_info:
        logger.debug(f'Zones to check in {loc} => {len(config_zone_info[loc])}')
    if len(config_zone_info) == 0:
        raise Exception('no valid zone listed in config file')

    edgecontainer_api_endpoint_override = os.environ.get("EDGE_CONTAINER_API_ENDPOINT_OVERRIDE")
    edgenetwork_api_endpoint_override = os.environ.get("EDGE_NETWORK_API_ENDPOINT_OVERRIDE")

    if edgecontainer_api_endpoint_override is not None and edgecontainer_api_endpoint_override != "":
        op = client_options.ClientOptions(api_endpoint=urlparse(edgecontainer_api_endpoint_override).netloc)
        ec_client = edgecontainer.EdgeContainerClient(client_options=op)
    else:  # use the default prod endpoint
        ec_client = edgecontainer.EdgeContainerClient()

    if edgenetwork_api_endpoint_override is not None and edgenetwork_api_endpoint_override != "":
        op = client_options.ClientOptions(api_endpoint=urlparse(edgenetwork_api_endpoint_override).netloc)
        en_client = edgenetwork.EdgeNetworkClient(client_options=op)
    else:  # use the default prod endpoint
        en_client = edgenetwork.EdgeNetworkClient()

    cb_client = cloudbuild.CloudBuildClient()

    '''
maintenancePolicy:
  window:
    recurringWindow:
      recurrence: FREQ=WEEKLY;BYDAY=SA
      window:
        endTime: '2023-01-01T17:00:00Z'
        startTime: '2023-01-01T08:00:00Z'
    '''

    # if cluster not present, skip this cluster
    count = 0
    for loc in config_zone_info:
        # Get all the clusters in the location,
        # the GDCE Zone info is in "control_plane"
        # maintain window info is in "maintenance_policy.window"
        req_c = edgecontainer.ListClustersRequest(
            parent=ec_client.common_location_path(params.project_id, loc)
        )
        res_pager_c = ec_client.list_clusters(req_c)
        cl_list = [c for c in res_pager_c]  # all the clusters in the location
        for z in config_zone_info[loc]:
            # filter the cluster in the GDCE zone, should be at most 1
            zone_cluster_list = [c for c in cl_list if c.control_plane.local.node_location
                                 == config_zone_info[loc][z]['NODE_LOCATION']]
            if len(zone_cluster_list) == 0:
                logger.warning(f'No lcp cluster found in {z}')
                continue
            elif len(zone_cluster_list) > 1:
                logger.warning(f'More than 1 lcp clusters found in {z}')
            logger.debug(zone_cluster_list)
            rw = zone_cluster_list[0].maintenance_policy.window.recurring_window  # cluster in this GDCE zone
            # Validate the start_time, end_time and rrule string of the maintenance window
            has_update = False
            if (rw.recurrence == config_zone_info[loc][z]['MAINTENANCE_WINDOW_RECURRENCE'] and
                    rw.window.start_time == config_zone_info[loc][z]['MAINTENANCE_WINDOW_START'] and
                    rw.window.end_time == config_zone_info[loc][z]['MAINTENANCE_WINDOW_END']):
                # get subnet vlan ids and ip addresses of this GDCE Zone
                req_n = edgenetwork.ListSubnetsRequest(
                    parent=f'{en_client.common_location_path(params.project_id, loc)}/zones/{z}'
                )
                res_pager_n = en_client.list_subnets(req_n)
                subnet_list = [{'vlan_id': net.vlan_id, 'ipv4_cidr': sorted(net.ipv4_cidr)} for net in res_pager_n]
                subnet_list.sort(key=lambda x: x['vlan_id'])
                logger.debug(subnet_list)
                try:
                    # as of now we only need to consider vlan_id for GDCE device (config-8)
                    # Needs to compare ipv4_cidr if this script applies to GDCE rack (config-1 or config-2)
                    # config_subnet_list = [{'vlan_id': int(v), 'ipv4_cidr': [n]} for v, n in zip(
                    #     config_zone_info[loc][z]['SUBNET_VLANS'].split(','),
                    #     config_zone_info[loc][z]['SUBNET_IPV4_ADDRESSES'].split(',')
                    # )].sort(key=lambda x: x['vlan_id'])
                    # if subnet_list != config_subnet_list:
                    #     has_update = True
                    if [n['vlan_id'] for n in subnet_list] != \
                            [int(v) for v in config_zone_info[loc][z]['SUBNET_VLANS'].split(',')].sort():
                        has_update = True
                except Exception as err:
                    logger.error(err)
            else:
                has_update = True
            if not has_update:
                continue
            # trigger cloudbuild to initiate the cluster updating
            repo_source = cloudbuild.RepoSource()
            repo_source.branch_name = config_zone_info[loc][z]['SYNC_BRANCH']
            repo_source.substitutions = {
                "_NODE_LOCATION": z
            }
            req = cloudbuild.RunBuildTriggerRequest(
                name=params.cloud_build_trigger,
                source=repo_source
            )
            logger.debug(req)
            try:
                logger.info(f'triggering cloud build for {z}')
                logger.info(f'trigger: {params.cloud_build_trigger}')
                opr = cb_client.run_build_trigger(request=req)
                # response = opr.result()
            except Exception as err:
                logger.error(err)

            count += len(config_zone_info[loc])

    return f'total zones triggered = {count}'

class ClusterIntentReader:
    def __init__(self, repo, branch, sourceOfTruth, token):
        self.repo = repo
        self.branch = branch
        self.sourceOfTruth = sourceOfTruth
        self.token = token

    def retrieve_source_of_truth(self):
        url = self._get_url()

        resp = requests.get(url, headers=self._get_headers())

        if resp.status_code == 200:
            return resp.text
        else:
            raise Exception(f"Unable to retrieve source of truth with status code ({resp.status_code})")

    def _get_url(self):
        parse_result = urlparse(f"https://{self.repo}")

        if parse_result.netloc == "github.com":
            # Remove .git suffix used in git web url
            path = parse_result.path.split('.')[0]

            return f"https://raw.githubusercontent.com{path}/{self.branch}/{self.sourceOfTruth}"
        elif parse_result.netloc == "gitlab.com":
            path = parse_result.path.split('.')[0]
            
            # projectid is url encoded: org%2Fproject%2Frepo_name
            project_id = path[1:].replace('/', '%2F')

            return f"https://gitlab.com/api/v4/projects/{project_id}/repository/files/{self.sourceOfTruth}/raw?ref={self.branch}&private_token={self.token}"
        else:
            raise Exception("Unsupported git provider")

    def _get_headers(self):
        headers = CaseInsensitiveDict()

        parse_result = urlparse(f"https://{self.repo}")

        if parse_result.netloc == "github.com":
            headers["Authorization"] = f"token {self.token}"
            return headers
        elif parse_result.netloc == "gitlab.com":
            return headers
        else:
            raise Exception("Unsupported git provider")
        
def get_git_token_from_secrets_manager(secrets_project_id, secret_id, version_id="latest"):
    client = secretmanager.SecretManagerServiceClient()

    name = f"projects/{secrets_project_id}/secrets/{secret_id}/versions/{version_id}"

    response = client.access_secret_version(request={"name": name})

    crc32c = google_crc32c.Checksum()
    crc32c.update(response.payload.data)
    if response.payload.data_crc32c != int(crc32c.hexdigest(), 16):
        raise Exception("Data corruption detected.")

    return response.payload.data.decode("UTF-8")