# GDCE Cluster Provisioner

This solution automatically provisions GDCE clusters as zones are turned up. This removes the need to wait and manually trigger automation after the turn up has complete. [Automated GDCE Cluster Provisioning TDD](https://docs.google.com/document/d/1nRi-V_vzmorZ7It8aPxuXnvZyih8n3wn1G73me6ACco/edit?resourcekey=0-W6AvnU-WWI1ynk4ETH0wAQ&tab=t.0#heading=h.8pa838wf1v4e)

## Quickstart

Deploy Cloudbuild and GCS resources

```
terraform plan
terraform apply
```

Update the `cluster-intent-registry.csv` file with new cluster intents. Trigger the manual cloud build trigger passing in the `_NODE_LOCATION` substitution to target a particular zone. 