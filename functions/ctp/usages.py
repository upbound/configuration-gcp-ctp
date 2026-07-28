"""04-usages — deletion-order Usage guards.

Base guards: the UXP Helm Release must finish uninstalling before the GKE
cluster is deleted, and the GKE cluster must be gone before the network
(VPC/Subnetwork, owned by the composed Network XR) is removed. Guards target the
composed GKE + Network XRs, not the underlying managed resources (which the
lower configs own).

Add-on guards: every child-cluster Release/Object added by an add-on (k8gb,
ArgoCD) also gets an `of: GKE, by: <resource>` guard, so it finishes
uninstalling before the GKE cluster/kubeconfig is torn out from under it
(otherwise the child Objects orphan-finalize). Emitted only when the add-on is
enabled.
"""

from crossplane.function import resource

from .prelude import stamp


def _emit_gke_usage(rsp, id_val, cr_name, by_api_version, by_kind, by_name,
                    reason, config):
    """Emit an `of: GKE, by: <resource>` Usage guarding a child-cluster
    resource against premature GKE deletion."""
    usage = {
        "apiVersion": "protection.crossplane.io/v1beta1",
        "kind": "Usage",
        "metadata": {
            "name": f"{id_val}-{cr_name}",
            "namespace": config["namespace"],
            "annotations": {
                "crossplane.io/composition-resource-name": cr_name
            }
        },
        "spec": {
            "of": {
                "apiVersion": "gcp.platform.upbound.io/v1alpha1",
                "kind": "GKE",
                "resourceRef": {
                    "name": id_val,
                    "namespace": config["namespace"]
                }
            },
            "by": {
                "apiVersion": by_api_version,
                "kind": by_kind,
                "resourceRef": {
                    "name": by_name
                }
            },
            "reason": reason,
            "replayDeletion": True
        }
    }
    stamp(usage, config)
    resource.update(rsp.desired.resources[cr_name], usage)


def add_usage_resources(rsp, id_val, config, k8gb_enabled=False,
                        argocd_enabled=False):
    usage_release_gke = {
        "apiVersion": "protection.crossplane.io/v1beta1",
        "kind": "Usage",
        "metadata": {
            "name": f"{id_val}-usage-release-gke",
            "namespace": config["namespace"],
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
                    "namespace": config["namespace"]
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
            "namespace": config["namespace"],
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
                    "namespace": config["namespace"]
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

    if k8gb_enabled or argocd_enabled:
        _emit_gke_usage(
            rsp, id_val, "usage-envoy-gateway-gke",
            "helm.m.crossplane.io/v1beta1", "Release",
            f"{id_val}-envoy-gateway",
            "Envoy Gateway Release must finish uninstalling before the GKE cluster is deleted",
            config)
        # The EnvoyProxy + GatewayClass child Objects also guard the GKE cluster
        # so they do not orphan-finalize when the cluster/kubeconfig is torn out.
        for cr_name in ("envoy-proxy-config", "gateway-class"):
            _emit_gke_usage(
                rsp, id_val, f"usage-{cr_name}-gke",
                "kubernetes.m.crossplane.io/v1alpha1", "Object",
                f"{id_val}-{cr_name}",
                f"Envoy Gateway {cr_name} Object must be removed before the GKE cluster is deleted",
                config)

    if k8gb_enabled:
        _emit_gke_usage(
            rsp, id_val, "usage-k8gb-gke",
            "helm.m.crossplane.io/v1beta1", "Release",
            f"{id_val}-k8gb",
            "k8gb Release must finish uninstalling before the GKE cluster is deleted",
            config)
        # The observe-only CoreDNS Object also guards the GKE cluster so it does
        # not orphan-finalize when the cluster/kubeconfig is torn out first.
        _emit_gke_usage(
            rsp, id_val, "usage-k8gb-coredns-gke",
            "kubernetes.m.crossplane.io/v1alpha1", "Object",
            f"{id_val}-k8gb-coredns",
            "k8gb CoreDNS observe Object must be removed before the GKE cluster is deleted",
            config)

    if argocd_enabled:
        _emit_gke_usage(
            rsp, id_val, "usage-argocd-gke",
            "helm.m.crossplane.io/v1beta1", "Release",
            f"{id_val}-argocd",
            "ArgoCD Release must finish uninstalling before the GKE cluster is deleted",
            config)
        # Every child-cluster ArgoCD Object also guards the GKE cluster.
        for cr_name in ("argocd-issuer", "argocd-cert", "argocd-gateway",
                        "argocd-httproute", "argocd-app"):
            _emit_gke_usage(
                rsp, id_val, f"usage-{cr_name}-gke",
                "kubernetes.m.crossplane.io/v1alpha1", "Object",
                f"{id_val}-{cr_name}",
                f"ArgoCD {cr_name} Object must be removed before the GKE cluster is deleted",
                config)
