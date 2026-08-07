"""01-network — VPC network via the Network XR.

Composes a single Network XR from configuration-gcp-network (kind `Network`,
gcp.platform.upbound.io/v1alpha1) instead of emitting raw compute Network +
Subnetwork managed resources. The Network XR creates the VPC network and
subnetwork (with the secondary ranges GKE needs) so the GKE XR can run in
VPC-native mode.
"""

from crossplane.function import resource

from .prelude import stamp


def add_network_resources(rsp, id_val, location, provider_config, mgmt_policies,
                          config):
    network = {
        "apiVersion": "gcp.platform.upbound.io/v1alpha1",
        "kind": "Network",
        "metadata": {
            "name": id_val,
            "namespace": config["namespace"],
            "annotations": {
                "crossplane.io/composition-resource-name": "network"
            }
        },
        "spec": {
            "parameters": {
                "id": id_val,
                "region": location,
                "managementPolicies": mgmt_policies,
                "providerConfigName": provider_config
            }
        }
    }
    # XR; the underlying composition manages the VPC/subnetwork MRs.
    stamp(network, config)
    resource.update(rsp.desired.resources["network"], network)
