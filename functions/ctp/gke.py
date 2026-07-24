"""02-gke — GKE cluster via the GKE XR.

Composes a single GKE XR from configuration-gcp-gke (kind `GKE`,
gcp.platform.upbound.io/v1alpha1) instead of emitting a raw Cluster + NodePool
+ ProviderConfigs. The GKE composition:
  * creates the GKE Cluster + worker NodePool (VPC-native, selecting the
    Network XR's network/subnetwork) with Workload Identity enabled,
  * writes the kubeconfig connection secret,
  * creates a Helm and a Kubernetes ProviderConfig BOTH named `<id>`, and
  * surfaces cluster metadata at status.gke.

Because the ProviderConfigs are named `<id>`, every downstream module here
(uxp, backup, workload_identity, licensing, knative, runtime_config) keeps
referencing `providerConfigRef.name = id_val` unchanged.

The ControlPlane XRD keeps the GCP-idiomatic `nodes.machineType`; it is mapped
to the GKE XR's `nodes.instanceType` here.
"""

from crossplane.function import resource

from .prelude import stamp


def add_gke_resources(rsp, id_val, location, project, provider_config, version,
                      nodes, mgmt_policies, config):
    gke = {
        "apiVersion": "gcp.platform.upbound.io/v1alpha1",
        "kind": "GKE",
        "metadata": {
            "name": id_val,
            "namespace": config["namespace"],
            "annotations": {
                "crossplane.io/composition-resource-name": "gke"
            }
        },
        "spec": {
            "parameters": {
                "id": id_val,
                "project": project,
                "region": location,
                "version": version,
                "workloadIdentity": {
                    "enabled": True
                },
                "nodes": {
                    "count": nodes.get("count", 2),
                    "instanceType": nodes.get("machineType", "e2-standard-2")
                },
                "managementPolicies": mgmt_policies,
                "providerConfigName": provider_config
            }
        }
    }
    stamp(gke, config)
    resource.update(rsp.desired.resources["gke"], gke)
