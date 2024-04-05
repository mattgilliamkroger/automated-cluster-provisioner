import functions_framework
import sys
import os
import io
import flask
import csv
import logging
from google.api_core import client_options
from google.cloud import edgecontainer
from google.cloud import storage
from google.cloud.devtools import cloudbuild


@functions_framework.http
def zone_watcher(req: flask.Request):

    proj_id = os.environ.get("GOOGLE_CLOUD_PROJECT") # This is the project id of where the csv file located
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
    # NODE_LOCATION	MACHINE_PROJECT_ID	FLEET_PROJECT_ID	CLUSTER_NAME	LOCATION	NODE_COUNT	EXTERNAL_LOAD_BALANCER_IPV4_ADDRESS_POOLS	SYNC_REPO	SYNC_BRANCH	SYNC_DIR	GIT_TOKEN_SECRETS_MANAGER_NAME
    # us-central1-edge-den25349	cloud-alchemist-machines	gmec-developers-1	lcp-den29	us-central1	1	172.17.34.96-172.17.34.100	https://gitlab.com/gcp-solutions-public/retail-edge/gdce-shyguy-internal/primary-root-repo	main	/config/clusters/den29/meta	shyguy-internal-pat
    config_zone_info = {}
    sto_client = storage.Client(project=proj_id)
    blob = storage.Blob.from_string(uri=gcs_config_uri, client=sto_client)
    zone_config_fio = io.StringIO(blob.download_as_bytes().decode())  # download the content to memory
    rdr = csv.DictReader(zone_config_fio)  # will raise exception if csv parsing fails
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

    if edgecontainer_api_endpoint_override is not None:
        op = client_options.ClientOptions(api_endpoint=edgecontainer_api_endpoint_override)
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
