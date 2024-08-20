import os
import sys

sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from watchers.src.main import *

os.environ['GOOGLE_CLOUD_PROJECT'] = 'cloud-alchemists-sandbox'
os.environ['REGION'] = 'us-central1'
# os.environ['CONFIG_CSV'] = 'gs://gdce-cluster-provisioner-bucket/cluster-intent-registry.csv'
os.environ['CB_TRIGGER_NAME'] = 'gdce-cluster-provisioner-trigger'
# os.environ['LOG_LEVEL'] = 'debug'
os.environ['EDGE_CONTAINER_API_ENDPOINT_OVERRIDE'] = 'https://staging-edgecontainer.sandbox.googleapis.com/'
os.environ['EDGE_NETWORK_API_ENDPOINT_OVERRIDE'] = 'https://staging-edgenetwork.sandbox.googleapis.com/'
os.environ['HARDWARE_MANAGMENT_API_ENDPOINT_OVERRIDE'] = 'https://staging-gdchardwaremanagement.sandbox.googleapis.com/'

# zone_watcher(None)
# cluster_watcher(None)
zone_active_metric(None)

# os.environ['GOOGLE_CLOUD_PROJECT'] = 'gmec-developers-1'
# os.environ['REGION'] = 'us-central1'
# os.environ['CONFIG_CSV'] = 'gs://gdce-cluster-provisioner-bucket/cluster-intent-registry.csv'
# os.environ['CB_TRIGGER_NAME'] = 'gdce-cluster-updater-trigger'
# os.environ['LOG_LEVEL'] = 'debug'
# os.environ['EDGE_CONTAINER_API_ENDPOINT_OVERRIDE'] = 'staging-edgecontainer.sandbox.googleapis.com'

# cluster_watcher(None)