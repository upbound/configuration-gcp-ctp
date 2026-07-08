"""04-usages — deletion-order Usage guards.

The UXP Helm Release must finish uninstalling and the worker NodePool must be
gone before the GKE cluster is deleted, and the cluster must be gone before
the Subnetwork / Network are removed.

Ported from configuration-azure-ctp/usages.py. GCP has no ResourceGroup, so
the chain ends at the Network rather than a ResourceGroup, and there is an
extra NodePool→Cluster guard because GKE models the worker pool as a separate
managed resource (AKS used an inline default node pool).
"""

from crossplane.function import resource

from .prelude import stamp


def add_usage_resources(rsp, id_val, config):
    usage_release_cluster = {
        "apiVersion": "protection.crossplane.io/v1beta1",
        "kind": "Usage",
        "metadata": {
            "name": f"{id_val}-usage-release-cluster",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "usage-release-cluster"
            }
        },
        "spec": {
            "of": {
                "apiVersion": "container.gcp.m.upbound.io/v1beta2",
                "kind": "Cluster",
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

    usage_nodepool_cluster = {
        "apiVersion": "protection.crossplane.io/v1beta1",
        "kind": "Usage",
        "metadata": {
            "name": f"{id_val}-usage-nodepool-cluster",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "usage-nodepool-cluster"
            }
        },
        "spec": {
            "of": {
                "apiVersion": "container.gcp.m.upbound.io/v1beta2",
                "kind": "Cluster",
                "resourceRef": {
                    "name": id_val,
                    "namespace": "default"
                }
            },
            "by": {
                "apiVersion": "container.gcp.m.upbound.io/v1beta2",
                "kind": "NodePool",
                "resourceRef": {
                    "name": f"{id_val}-nodes"
                }
            },
            "reason": "Worker NodePool must be deleted before the GKE cluster is deleted",
            "replayDeletion": True
        }
    }

    usage_cluster_subnetwork = {
        "apiVersion": "protection.crossplane.io/v1beta1",
        "kind": "Usage",
        "metadata": {
            "name": f"{id_val}-usage-cluster-subnetwork",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "usage-cluster-subnetwork"
            }
        },
        "spec": {
            "of": {
                "apiVersion": "compute.gcp.m.upbound.io/v1beta1",
                "kind": "Subnetwork",
                "resourceRef": {
                    "name": f"{id_val}-gke",
                    "namespace": "default"
                }
            },
            "by": {
                "apiVersion": "container.gcp.m.upbound.io/v1beta2",
                "kind": "Cluster",
                "resourceRef": {
                    "name": id_val
                }
            },
            "reason": "GKE cluster must be fully deleted before the subnetwork is removed",
            "replayDeletion": True
        }
    }

    usage_subnetwork_network = {
        "apiVersion": "protection.crossplane.io/v1beta1",
        "kind": "Usage",
        "metadata": {
            "name": f"{id_val}-usage-subnetwork-network",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "usage-subnetwork-network"
            }
        },
        "spec": {
            "of": {
                "apiVersion": "compute.gcp.m.upbound.io/v1beta1",
                "kind": "Network",
                "resourceRef": {
                    "name": id_val,
                    "namespace": "default"
                }
            },
            "by": {
                "apiVersion": "compute.gcp.m.upbound.io/v1beta1",
                "kind": "Subnetwork",
                "resourceRef": {
                    "name": f"{id_val}-gke"
                }
            },
            "reason": "Subnetwork must be fully deleted before the network is removed",
            "replayDeletion": True
        }
    }

    usages = (
        usage_release_cluster,
        usage_nodepool_cluster,
        usage_cluster_subnetwork,
        usage_subnetwork_network,
    )
    for usage in usages:
        stamp(usage, config)
    resource.update(rsp.desired.resources["usage-release-cluster"], usage_release_cluster)
    resource.update(rsp.desired.resources["usage-nodepool-cluster"], usage_nodepool_cluster)
    resource.update(rsp.desired.resources["usage-cluster-subnetwork"], usage_cluster_subnetwork)
    resource.update(rsp.desired.resources["usage-subnetwork-network"], usage_subnetwork_network)
