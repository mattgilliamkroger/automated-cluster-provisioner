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
from google.api_core import client_options, exceptions
from google.cloud import edgecontainer
from google.cloud import edgenetwork
from google.cloud import secretmanager
from google.cloud import gdchardwaremanagement_v1alpha
from google.cloud.gdchardwaremanagement_v1alpha import Zone
from google.cloud.devtools import cloudbuild
from google.cloud import monitoring_v3
from google.protobuf.timestamp_pb2 import Timestamp
from dateutil.parser import parse
from typing import Dict

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
        source_of_truth_path=source_of_truth_path,
    )


@functions_framework.http
def zone_watcher(req: flask.Request):
    params = get_parameters_from_environment()

    logger.info(f'Running zone watcher for: proj_id={params.project_id},sot={params.source_of_truth_repo}/{params.source_of_truth_branch}/{params.source_of_truth_path}, cb_trigger={params.cloud_build_trigger}')
    
    config_zone_info = {}
    token = get_git_token_from_secrets_manager(params.secrets_project_id, params.git_secret_id)
    intent_reader = ClusterIntentReader(params.source_of_truth_repo, params.source_of_truth_branch, params.source_of_truth_path, token)
    zone_config_fio = intent_reader.retrieve_source_of_truth()
    rdr = csv.DictReader(io.StringIO(zone_config_fio))  # will raise exception if csv parsing fails
    machine_proj_loc = set()

    for row in rdr:
        if row['location'] not in config_zone_info.keys():
            config_zone_info[row['location']] = {}
        config_zone_info[row['location']][row['store_id']] = row
        machine_proj_loc.add((row['machine_project_id'], row['location']))
    for loc in config_zone_info:
        logger.debug(f'Zones to check in {loc} => {len(config_zone_info[loc])}')
    if len(config_zone_info) == 0:
        raise Exception('no valid zone listed in config file')

    edgecontainer_api_endpoint_override = os.environ.get("EDGE_CONTAINER_API_ENDPOINT_OVERRIDE")
    if edgecontainer_api_endpoint_override:
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
    for location in config_zone_info:
        for store_id in config_zone_info[location]:
            machine_project_id = config_zone_info[location][store_id]['machine_project_id']
            zone_store_id = f'projects/{machine_project_id}/locations/{location}/zones/{store_id}'
            try:
                if config_zone_info[location][store_id]['zone_name']:
                    zone = config_zone_info[location][store_id]['zone_name']
                    zone_name_retrieved_from_api = False
                else:
                    zone = get_zone_name(zone_store_id)
                    zone_name_retrieved_from_api = True
            except:
                logger.error(f'Zone for store {store_id} cannot be found, skipping.', exc_info=True)
                continue
            
            if zone not in machine_lists:
                logger.warning(f'No machine found in zone {zone}')
                continue

            count_of_free_machines = 0
            for m in machine_lists[zone]:
                if len(m.hosted_node.strip()) > 0:  # if there is any value, consider there is a cluster
                    logger.info(f'ZONE {zone}: {m.name} already used by {m.hosted_node}')
                else:
                    logger.info(f'ZONE {zone}: {m.name} is a free node')
                    count_of_free_machines = count_of_free_machines+1
            if count_of_free_machines >= int(config_zone_info[location][store_id]["node_count"]):
                logger.info(f'ZONE {zone}: There are enough free  nodes to create cluster')
            else:
                logger.info(f'ZONE {zone}: Not enough free  nodes to create cluster. Need {str(config_zone_info[location][store_id]["node_count"])} but have {str(count_of_free_machines)} free nodes')
                continue

            if zone_name_retrieved_from_api and not verify_zone_state(zone_store_id, config_zone_info[location][store_id]['recreate_on_delete']):
                logger.info(f'Zone: {zone}, Store: {store_id} is not in expected state! skipping..')
                continue

            # trigger cloudbuild to initiate the cluster building
            repo_source = cloudbuild.RepoSource()
            repo_source.branch_name = config_zone_info[location][store_id]['sync_branch']
            repo_source.substitutions = {
                "_STORE_ID": store_id,
                "_ZONE": zone
            }
            req = cloudbuild.RunBuildTriggerRequest(
                name=params.cloud_build_trigger,
                source=repo_source
            )
            logger.debug(req)
            try:
                logger.info(f'triggering cloud build for {zone}')
                logger.info(f'trigger: {params.cloud_build_trigger}')
                opr = cb_client.run_build_trigger(request=req)
                # response = opr.result()
            except Exception as err:
                logger.error(err)

            count += len(config_zone_info[location])

    logger.info(f'total zones triggered = {count}')

    return f'total zones triggered = {count}'


@functions_framework.http
def cluster_watcher(req: flask.Request):
    params = get_parameters_from_environment()

    logger.info(f'proj_id = {params.project_id}')
    logger.info(f'cb_trigger = {params.cloud_build_trigger}')

    config_zone_info = {}
    token = get_git_token_from_secrets_manager(params.secrets_project_id, params.git_secret_id)
    intent_reader = ClusterIntentReader(params.source_of_truth_repo, params.source_of_truth_branch, params.source_of_truth_path, token)
    zone_config_fio = intent_reader.retrieve_source_of_truth()
    rdr = csv.DictReader(io.StringIO(zone_config_fio))  # will raise exception if csv parsing fails
    for row in rdr:
        if row['location'] not in config_zone_info.keys():
            config_zone_info[row['location']] = {}
        config_zone_info[row['location']][row['store_id']] = row
    for location in config_zone_info:
        logger.debug(f'Stores to check in {location} => {len(config_zone_info[location])}')
    if len(config_zone_info) == 0:
        raise Exception('no valid zone listed in config file')

    edgecontainer_api_endpoint_override = os.environ.get("EDGE_CONTAINER_API_ENDPOINT_OVERRIDE")
    edgenetwork_api_endpoint_override = os.environ.get("EDGE_NETWORK_API_ENDPOINT_OVERRIDE")

    if edgecontainer_api_endpoint_override:
        op = client_options.ClientOptions(api_endpoint=urlparse(edgecontainer_api_endpoint_override).netloc)
        ec_client = edgecontainer.EdgeContainerClient(client_options=op)
    else:  # use the default prod endpoint
        ec_client = edgecontainer.EdgeContainerClient()

    if edgenetwork_api_endpoint_override:
        op = client_options.ClientOptions(api_endpoint=urlparse(edgenetwork_api_endpoint_override).netloc)
        en_client = edgenetwork.EdgeNetworkClient(client_options=op)
    else:  # use the default prod endpoint
        en_client = edgenetwork.EdgeNetworkClient()

    cb_client = cloudbuild.CloudBuildClient()

    # if cluster not present, skip this cluster
    count = 0
    for location in config_zone_info:
        # Get all the clusters in the location,
        # the GDCE Zone info is in "control_plane"
        # maintain window info is in "maintenance_policy.window"
        req_c = edgecontainer.ListClustersRequest(
            parent=ec_client.common_location_path(params.project_id, location)
        )
        res_pager_c = ec_client.list_clusters(req_c)
        cl_list = [c for c in res_pager_c]  # all the clusters in the location
        for store_id in config_zone_info[location]:
            machine_project_id = config_zone_info[location][store_id]['machine_project_id']
            zone_store_id = f'projects/{machine_project_id}/locations/{location}/zones/{store_id}'
            try:
                if config_zone_info[location][store_id]['zone_name']:
                    zone = config_zone_info[location][store_id]['zone_name']
                else:
                    zone = get_zone_name(zone_store_id)
            except:
                logger.error(f'Zone for store {store_id} cannot be found, skipping.', exc_info=True)
                continue

            # filter the cluster in the GDCE zone, should be at most 1
            zone_cluster_list = [c for c in cl_list if c.control_plane.local.node_location
                                 == zone]
            if len(zone_cluster_list) == 0:
                logger.warning(f'No lcp cluster found in {zone}')
                continue
            elif len(zone_cluster_list) > 1:
                logger.warning(f'More than 1 lcp clusters found in {zone}')
            logger.debug(zone_cluster_list)
            rw = zone_cluster_list[0].maintenance_policy.window.recurring_window  # cluster in this GDCE zone
            # Validate the start_time, end_time and rrule string of the maintenance window
            has_update = False

            if (not config_zone_info[location][store_id]['maintenance_window_recurrence'] or
                not config_zone_info[location][store_id]['maintenance_window_start'] or
                not config_zone_info[location][store_id]['maintenance_window_end']
                ):
                # One of the MW properties is not set, so assume no update needs to be made
                has_update = False
            elif (rw.recurrence != config_zone_info[location][store_id]['maintenance_window_recurrence'] or
                    rw.window.start_time != parse(config_zone_info[location][store_id]['maintenance_window_start']) or
                    rw.window.end_time != parse(config_zone_info[location][store_id]['maintenance_window_end'])):
                logger.info("Maintenance window requires update")
                logger.info(f"Actual values (recurrence={rw.recurrence}, start_time={rw.window.start_time}, end_time={rw.window.end_time})")
                logger.info(f"Desired values (recurrence={config_zone_info[location][store_id]['maintenance_window_recurrence']}, start_time={config_zone_info[location][store_id]['maintenance_window_start']}, end_time={config_zone_info[location][store_id]['maintenance_window_end']})")
                has_update = True

            # get subnet vlan ids and ip addresses of this GDCE Zone
            req_n = edgenetwork.ListSubnetsRequest(
                parent=f'{en_client.common_location_path(config_zone_info[location][store_id]["machine_project_id"], location)}/zones/{zone}'
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
                for desired_subnet in config_zone_info[location][store_id]['subnet_vlans'].split(','):
                    try:
                        vlan_id = int(desired_subnet)
                    except Exception as err:
                        logger.error("unable to convert vlan to an int", err)

                    if vlan_id not in [n['vlan_id'] for n in subnet_list]:
                        logger.info(f"No vlan created for vlan: {vlan_id}")
                        has_update = True

                for actual_vlan_id in [n['vlan_id'] for n in subnet_list]:
                    if actual_vlan_id not in [int(v) for v in config_zone_info[location][store_id]['subnet_vlans'].split(',')]:
                        logger.error(f"VLAN {actual_vlan_id} is defined in the environment, but not in the source of truth. The subnet will need to be manually deleted from the environment.")
            except Exception as err:
                logger.error(err)

            if not has_update:
                continue
            # trigger cloudbuild to initiate the cluster updating
            repo_source = cloudbuild.RepoSource()
            repo_source.branch_name = config_zone_info[location][store_id]['sync_branch']
            repo_source.substitutions = {
                "_STORE_ID": store_id,
                "_ZONE": zone
            }
            req = cloudbuild.RunBuildTriggerRequest(
                name=params.cloud_build_trigger,
                source=repo_source
            )
            logger.debug(req)
            try:
                logger.info(f'triggering cloud build for {zone}')
                logger.info(f'trigger: {params.cloud_build_trigger}')
                opr = cb_client.run_build_trigger(request=req)
                # response = opr.result()
            except Exception as err:
                logger.error(err)

            count += len(config_zone_info[location])

    return f'total zones triggered = {count}'


@functions_framework.http
def zone_active_metric(req: flask.Request):
    params = get_parameters_from_environment()

    logger.info(
        f'Running zone active watcher in: proj_id={params.project_id}, sot={params.source_of_truth_repo}/{params.source_of_truth_branch}/{params.source_of_truth_path}')

    token = get_git_token_from_secrets_manager(params.secrets_project_id, params.git_secret_id)
    intent_reader = ClusterIntentReader(
        params.source_of_truth_repo, params.source_of_truth_branch,
        params.source_of_truth_path, token)
    zone_config_fio = intent_reader.retrieve_source_of_truth()
    rdr = csv.DictReader(io.StringIO(zone_config_fio))  # will raise exception if csv parsing fails

    time_series_data = []
    for row in rdr:
        f_proj_id = row['fleet_project_id']
        m_proj_id = f_proj_id if row['machine_project_id'] is None or len(row['machine_project_id']) == 0 else row['machine_project_id']
        loc = params.region if row['location'] is None or len(row['location']) == 0 else row['location']
        store_id = row['store_id']
        cl_name = row['cluster_name']
        full_zone_name = f'projects/{m_proj_id}/locations/{loc}/zones/{store_id}'
        b_generate_metric = False
        b_zone_found = False
        active_metric = 0  # 0 - inactive, 1 - active
        try:
            zone = get_zone(full_zone_name)
            logger.debug(f'{store_id} state = {Zone.State(zone.state).name}')
            b_zone_found = True
        except Exception as e:
            logger.debug(f'get_zone({store_id}) -> {type(e)}', exc_info=False)
            if isinstance(e, exceptions.ServerError):
                # if ServerError (API failure), treat zone as active and not to filter any alerts
                # any exception other than hw mgmt API failure, such as ClientError or generic exception
                # treat as non-existing zone (don't generate metric)
                b_generate_metric = True
                active_metric = 1

        if b_zone_found and zone.globally_unique_id is not None and len(zone.globally_unique_id.strip()) > 0:
            # only zones with globally_unique_id is considering as existing zones(generate metric)
            gdce_zone_name = zone.globally_unique_id.strip()
            b_generate_metric = True
            if zone.state == Zone.State.ACTIVE:
                active_metric = 1

        if not b_generate_metric:
            continue

        # Construct time series datapoints for each store
        timestamp = Timestamp()
        timestamp.GetCurrentTime()
        data_point = {
            'interval': {'end_time': timestamp},
            'value': {'int64_value': active_metric}
        }
        time_series_point = {
            'metric': {
                'type': 'custom.googleapis.com/gdc_zone_active',
                'labels': {
                    'fleet_project_id': f_proj_id,
                    'machine_project_id': m_proj_id,
                    'location': loc,
                    'store_id': store_id,
                    'zone_name': gdce_zone_name,
                    'cluster_name': cl_name,
                }
            },
            'resource': {
                'type': 'global',
                'labels': {
                    'project_id': f_proj_id
                }
            },
            'points': [data_point]
        }
        time_series_data.append(time_series_point)

    # send batch requests to metric
    m_client = monitoring_v3.MetricServiceClient()
    batch_size = 200
    for i in range(0, len(time_series_data), batch_size):
        request = monitoring_v3.CreateTimeSeriesRequest({
            'name': f'projects/{params.project_id}',
            'time_series': time_series_data[i:i + batch_size]
        })
        m_client.create_time_series(request)

    logger.debug(f'update datapoint for {[x["metric"]["labels"]["store_id"] for x in time_series_data]}')
    logger.debug(f'total zone active flag updated = {len(time_series_data)}')
    return f'total zone active flag updated = {len(time_series_data)}'


def get_gcp_compute_engine_metadata(project: str) -> Dict[str, str]:
    """Returns the compute engine metadata from the GCP project as a dict
    Args:
        project: project name or id
    Returns:
        metadata as a dict
    """
    from google.cloud import compute_v1
    client = compute_v1.ProjectsClient()
    request = compute_v1.GetProjectRequest(
        project=project,
    )
    response = client.get(request=request)
    metadata = {item.key: item.value for item in response.common_instance_metadata.items}
    return metadata


def get_zone(store_id: str) -> Zone:
    """Return Zone info.
    Args:
      store_id: name of zone which is store id usually
    Returns:
      Zone object
    """
    hardware_management_api_endpoint_override = os.environ.get('HARDWARE_MANAGMENT_API_ENDPOINT_OVERRIDE')
    if hardware_management_api_endpoint_override:
        op = client_options.ClientOptions(api_endpoint=urlparse(hardware_management_api_endpoint_override).netloc)
        client = gdchardwaremanagement_v1alpha.GDCHardwareManagementClient(client_options=op)
    else:
        client = gdchardwaremanagement_v1alpha.GDCHardwareManagementClient()

    return client.get_zone(name=store_id)


def get_zone_name(store_id: str) -> str:
    """Return Zone info.
    Args:
      store_id: name of zone which is store id usually
    Returns:
      rack zone name
    """
    return get_zone(store_id).globally_unique_id


def get_zone_state(store_id: str) -> Zone.State:
    """Return Zone info.
    Args:
      store_id: name of zone which is store id usually
    Returns:
      zone state
    """
    return get_zone(store_id).state


def verify_zone_state(store_id: str, recreate_on_delete: bool) -> bool:
    """Checks if zone is in right state to create.
    Args:
        store_id: name of zone which is store id usually
        recreate_on_delete: true if cluster needs to be recreated on delete.
    Returns:
        if cluster can be created or not
    """
    state = get_zone_state(store_id)
    if state == Zone.State.READY_FOR_CUSTOMER_FACTORY_TURNUP_CHECKS or state == Zone.State.CUSTOMER_FACTORY_TURNUP_CHECKS_FAILED:
        return True

    if state == Zone.State.READY_FOR_SITE_TURNUP and recreate_on_delete:
        logger.info(f'Store: {store_id} was already setup, but specified to recreate on delete!')
        return True
    
    return False


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
