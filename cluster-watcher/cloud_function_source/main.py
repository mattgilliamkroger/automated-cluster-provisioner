import functions_framework
import sys
import os
import io
import flask
import csv
import logging
from google.api_core import client_options
from google.cloud import edgecontainer
from google.cloud import edgenetwork
from google.cloud import storage
from google.cloud.devtools import cloudbuild


@functions_framework.http
def cluster_watcher(req: flask.Request):
    proj_id = os.environ.get("GOOGLE_CLOUD_PROJECT")  # This is the project id of where the csv file located
    region = os.environ.get("REGION")
    gcs_config_uri = os.environ.get("CONFIG_CSV")
    # format: projects/<project-id>/locations/<location>/triggers/<trigger-name>
    # e.g. projects/daniel-test-proj-411311/locations/us-central1/triggers/test-trigger
    # location could be "global"
    cb_trigger = f'projects/{proj_id}/locations/{region}/triggers/{os.environ.get("CB_TRIGGER_NAME")}'
    if proj_id is None:
        raise Exception('missing GOOGLE_CLOUD_PROJECT, (gcs csv file project)')
    if region is None:
        raise Exception('missing REGION (us-central1)')
    if gcs_config_uri is None:
        raise Exception('missing CONFIG_CSV (gs://<bucket_name>/<csv_file_path>)')
    if cb_trigger is None:
        raise Exception('missing CB_TRIGGER_NAME (projects/<project-id>/locations/<location>/triggers/<trigger-name>)')

    log_lvl = logging.DEBUG if os.environ.get("LOG_LEVEL").lower() == 'debug' else logging.INFO

    # set log level, default is INFO, unless has {debug: true} in request
    logger = logging.getLogger()
    logging.basicConfig(stream=sys.stdout, level=log_lvl)

    logger.info(f'proj_id = {proj_id}')
    logger.info(f'gcs_config_uri = {gcs_config_uri}')
    logger.info(f'cb_trigger = {cb_trigger}')
    logger.debug(f'log_lvl = {log_lvl}')

    # Get the CSV file from GCS containing target zones
    # "NODE_LOCATION",              "MACHINE_PROJECT_ID",           "FLEET_PROJECT_ID",         "CLUSTER_NAME", "LOCATION", "NODE_COUNT",   "EXTERNAL_LOAD_BALANCER_IPV4_ADDRESS_POOLS","SYNC_REPO",                                                                                "SYNC_BRANCH",  "SYNC_DIR",                     "GIT_TOKEN_SECRETS_MANAGER_NAME",   "ES_AGENT_SECRETS_MANAGER_NAME", "SUBNET_VLANS",    "SUBNET_IPV4_ADDRESSES",    "MAINTENANCE_WINDOW_START", "MAINTENANCE_WINDOW_END",   "MAINTENANCE_WINDOW_RECURRENCE"
    # "us-central1-edge-den25349",  "cloud-alchemists-machines",    "cloud-alchemists-sandbox", "lcp-den29",    "us-central1",  "1",        "172.17.34.96-172.17.34.100",               "https://gitlab.com/gcp-solutions-public/retail-edge/gdce-shyguy-internal/primary-root-repo","main",        "/config/clusters/den29/meta",  "shyguy-internal-pat",              "external-secret-agent-key"
    # "us-central1-edge-den59566",  "edgesites-baremetal-lab-qual", "cloud-alchemists-sandbox", "lcp-den84",    "us-central1",  "3",        "172.20.4.240-172.20.4.248",                "https://gitlab.com/gcp-solutions-public/retail-edge/gdce-shyguy-internal/primary-root-repo","main",        "/config/clusters/den84/meta",  "shyguy-internal-pat",              "external-secret-agent-key"
    config_zone_info = {}
    sto_client = storage.Client(project=proj_id)
    blob = storage.Blob.from_string(uri=gcs_config_uri, client=sto_client)
    zone_config_fio = io.StringIO(blob.download_as_bytes().decode())  # download the content to memory
    rdr = csv.DictReader(zone_config_fio)  # will raise exception if csv parsing fails
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

    if edgecontainer_api_endpoint_override is not None:
        op = client_options.ClientOptions(api_endpoint=edgecontainer_api_endpoint_override)
        ec_client = edgecontainer.EdgeContainerClient(client_options=op)
    else:  # use the default prod endpoint
        ec_client = edgecontainer.EdgeContainerClient()

    if edgenetwork_api_endpoint_override is not None:
        op = client_options.ClientOptions(api_endpoint=edgenetwork_api_endpoint_override)
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
            parent=ec_client.common_location_path(proj_id, loc)
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
                    parent=f'{en_client.common_location_path(proj_id, loc)}/zones/{z}'
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
                name=cb_trigger,
                source=repo_source
            )
            logger.debug(req)
            try:
                logger.info(f'triggering cloud build for {z}')
                logger.info(f'trigger: {cb_trigger}')
                opr = cb_client.run_build_trigger(request=req)
                # response = opr.result()
            except Exception as err:
                logger.error(err)

            count += len(config_zone_info[loc])

    return f'total zones triggered = {count}'
