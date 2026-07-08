"""01-network — VPC Network + Subnetwork.

GCP has no ResourceGroup equivalent — resources live directly in a Project
(spec.parameters.project). We emit a custom-mode VPC Network plus a single
Subnetwork with secondary IP ranges named "pods" and "services" so the GKE
cluster can run in VPC-native (alias-IP) mode.

Ported from configuration-azure-ctp/network.py, which created a ResourceGroup
+ VirtualNetwork + Subnet.
"""

from crossplane.function import resource

from .prelude import stamp

# Secondary range names referenced by the GKE cluster's ipAllocationPolicy.
PODS_RANGE_NAME = "pods"
SERVICES_RANGE_NAME = "services"


def add_network_resources(rsp, id_val, location, project, provider_config,
                          mgmt_policies, config):
    network = {
        "apiVersion": "compute.gcp.m.upbound.io/v1beta1",
        "kind": "Network",
        "metadata": {
            "name": id_val,
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "network"
            }
        },
        "spec": {
            "managementPolicies": mgmt_policies,
            "forProvider": {
                "project": project,
                # Custom-mode VPC: we manage the subnetwork explicitly rather
                # than letting GCP auto-create one per region.
                "autoCreateSubnetworks": False,
                "routingMode": "REGIONAL"
            },
            "providerConfigRef": {
                "name": provider_config,
                "kind": "ClusterProviderConfig"
            }
        }
    }
    stamp(network, config)
    resource.update(rsp.desired.resources["network"], network)

    subnetwork = {
        "apiVersion": "compute.gcp.m.upbound.io/v1beta1",
        "kind": "Subnetwork",
        "metadata": {
            "name": f"{id_val}-gke",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "subnetwork"
            }
        },
        "spec": {
            "managementPolicies": mgmt_policies,
            "forProvider": {
                "project": project,
                "region": location,
                "ipCidrRange": "10.0.0.0/16",
                "networkRef": {
                    "name": id_val
                },
                # Secondary ranges for VPC-native GKE (alias IPs). The GKE
                # cluster's ipAllocationPolicy references these by name.
                "secondaryIpRange": [
                    {
                        "rangeName": PODS_RANGE_NAME,
                        "ipCidrRange": "10.1.0.0/16"
                    },
                    {
                        "rangeName": SERVICES_RANGE_NAME,
                        "ipCidrRange": "10.2.0.0/20"
                    }
                ]
            },
            "providerConfigRef": {
                "name": provider_config,
                "kind": "ClusterProviderConfig"
            }
        }
    }
    stamp(subnetwork, config)
    resource.update(rsp.desired.resources["subnetwork"], subnetwork)
