"""04-usages — deletion-order Usage guards.

The UXP Helm Release must finish uninstalling before the GKE cluster is
deleted, and the GKE cluster must be gone before the network (VPC/Subnetwork,
owned by the composed Network XR) is removed. Mirrors configuration-aws-ctp /
-azure-ctp: guards target the composed GKE + Network XRs, not the underlying
managed resources (which the lower configs own).
"""

from crossplane.function import resource

from .prelude import stamp


def add_usage_resources(rsp, id_val, config):
    usage_release_gke = {
        "apiVersion": "protection.crossplane.io/v1beta1",
        "kind": "Usage",
        "metadata": {
            "name": f"{id_val}-usage-release-gke",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "usage-release-gke"
            }
        },
        "spec": {
            "of": {
                "apiVersion": "gcp.platform.upbound.io/v1alpha1",
                "kind": "GKE",
                "resourceRef": {
                    "name": id_val,
                    "namespace": "default"
                }
            },
            "by": {
                "apiVersion": "helm.m.crossplane.io/v1beta1",
                "kind": "Release",
                "resourceRef": {
                    "name": f"{id_val}-uxp"
                }
            },
            "reason": "UXP Helm Release must finish uninstalling before the GKE cluster is deleted",
            "replayDeletion": True
        }
    }

    usage_gke_network = {
        "apiVersion": "protection.crossplane.io/v1beta1",
        "kind": "Usage",
        "metadata": {
            "name": f"{id_val}-usage-gke-network",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "usage-gke-network"
            }
        },
        "spec": {
            "of": {
                "apiVersion": "gcp.platform.upbound.io/v1alpha1",
                "kind": "Network",
                "resourceRef": {
                    "name": id_val,
                    "namespace": "default"
                }
            },
            "by": {
                "apiVersion": "gcp.platform.upbound.io/v1alpha1",
                "kind": "GKE",
                "resourceRef": {
                    "name": id_val
                }
            },
            "reason": "GKE cluster must be fully deleted before the network is removed",
            "replayDeletion": True
        }
    }

    for usage in (usage_release_gke, usage_gke_network):
        stamp(usage, config)
    resource.update(rsp.desired.resources["usage-release-gke"], usage_release_gke)
    resource.update(rsp.desired.resources["usage-gke-network"], usage_gke_network)
