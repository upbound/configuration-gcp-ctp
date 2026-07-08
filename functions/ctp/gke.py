"""02-gke — GKE cluster (Cluster) + worker NodePool + ProviderConfig + connection secret.

provider-gcp-container exposes `Cluster` (GKE) and `NodePool`. Ported from
configuration-azure-ctp/aks.py.

GCP-specific differences from the Azure (AKS) sibling:

* The cluster is created with `removeDefaultNodePool: true` +
  `initialNodeCount: 1` and the real worker pool is a SEPARATE managed
  `NodePool` resource — the idiomatic upjet/Terraform GKE pattern. (AKS
  required an inline `defaultNodePool` on the cluster instead.)
* Workload Identity is enabled by setting `workloadIdentityConfig.workloadPool`
  to `<project>.svc.id.goog`; there is no OIDC issuer to read back. The node
  pool sets `workloadMetadataConfig.mode: GKE_METADATA` so pods can reach the
  GKE metadata server.
* `deletionProtection: false` so Crossplane can delete the cluster (upjet
  GKE clusters default this to true, which blocks deletion).
* The kubeconfig is written to a connection secret referenced by a Helm
  ProviderConfig so UXP can be installed onto the new cluster, mirroring AKS.
"""

from crossplane.function import resource
from crossplane.function.proto.v1 import run_function_pb2 as fnv1

from .network import PODS_RANGE_NAME, SERVICES_RANGE_NAME
from .prelude import stamp, workload_identity_pool


def add_gke_resources(rsp, id_val, location, project, provider_config, version,
                      nodes, mgmt_policies, config):
    cluster = {
        "apiVersion": "container.gcp.m.upbound.io/v1beta2",
        "kind": "Cluster",
        "metadata": {
            "name": id_val,
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "gke-cluster"
            }
        },
        "spec": {
            "managementPolicies": mgmt_policies,
            "forProvider": {
                "project": project,
                "location": location,
                "minMasterVersion": version,
                # Manage the worker pool as a standalone NodePool below.
                "removeDefaultNodePool": True,
                "initialNodeCount": 1,
                "networkingMode": "VPC_NATIVE",
                "networkRef": {
                    "name": id_val
                },
                "subnetworkRef": {
                    "name": f"{id_val}-gke"
                },
                "ipAllocationPolicy": {
                    "clusterSecondaryRangeName": PODS_RANGE_NAME,
                    "servicesSecondaryRangeName": SERVICES_RANGE_NAME
                },
                "workloadIdentityConfig": {
                    "workloadPool": workload_identity_pool(project)
                },
                # upjet GKE clusters default this to true, which blocks delete.
                "deletionProtection": False
            },
            "writeConnectionSecretToRef": {
                "name": f"{id_val}-gke-kubeconfig"
            },
            "providerConfigRef": {
                "name": provider_config,
                "kind": "ClusterProviderConfig"
            }
        }
    }
    stamp(cluster, config)
    resource.update(rsp.desired.resources["gke-cluster"], cluster)

    nodepool = {
        "apiVersion": "container.gcp.m.upbound.io/v1beta2",
        "kind": "NodePool",
        "metadata": {
            "name": f"{id_val}-nodes",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "gke-nodepool"
            }
        },
        "spec": {
            "managementPolicies": mgmt_policies,
            "forProvider": {
                "project": project,
                "location": location,
                "clusterRef": {
                    "name": id_val
                },
                "nodeCount": nodes.get("count", 2),
                "nodeConfig": {
                    "machineType": nodes.get("machineType", "e2-standard-2"),
                    "oauthScopes": [
                        "https://www.googleapis.com/auth/cloud-platform"
                    ],
                    # Required for GKE Workload Identity: pods reach the GKE
                    # metadata server to obtain federated tokens.
                    "workloadMetadataConfig": {
                        "mode": "GKE_METADATA"
                    }
                }
            },
            "providerConfigRef": {
                "name": provider_config,
                "kind": "ClusterProviderConfig"
            }
        }
    }
    stamp(nodepool, config)
    resource.update(rsp.desired.resources["gke-nodepool"], nodepool)

    # Helm ProviderConfig pointed at the GKE kubeconfig connection secret, so
    # the UXP Helm Release in uxp.py can target this new cluster.
    helm_pc = {
        "apiVersion": "helm.m.crossplane.io/v1beta1",
        "kind": "ProviderConfig",
        "metadata": {
            "name": id_val,
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "helm-provider-config",
                # ProviderConfigs have no native Ready condition — stamp
                # them ready so function-auto-ready aggregates correctly.
                "crossplane.io/ready": "True"
            }
        },
        "spec": {
            "credentials": {
                "source": "Secret",
                "secretRef": {
                    "name": f"{id_val}-gke-kubeconfig",
                    "namespace": "default",
                    "key": "kubeconfig"
                }
            }
        }
    }
    stamp(helm_pc, config)
    resource.update(rsp.desired.resources["helm-provider-config"], helm_pc)
    # ProviderConfigs have no native Ready condition. Set the function's
    # protobuf-level Ready=TRUE directly so the composite controller doesn't
    # treat them as unready. The annotation alone isn't enough — Crossplane's
    # composite aggregation reads this field from each pipeline function's
    # response.
    rsp.desired.resources["helm-provider-config"].ready = fnv1.Ready.READY_TRUE

    # Matching Kubernetes ProviderConfig so provider-kubernetes Object
    # resources (BackupConfig, RBAC, license, knative CR, runtime config, …)
    # target the new cluster.
    kubernetes_pc = {
        "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
        "kind": "ProviderConfig",
        "metadata": {
            "name": id_val,
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "kubernetes-provider-config",
                "crossplane.io/ready": "True"
            }
        },
        "spec": {
            "credentials": {
                "source": "Secret",
                "secretRef": {
                    "name": f"{id_val}-gke-kubeconfig",
                    "namespace": "default",
                    "key": "kubeconfig"
                }
            }
        }
    }
    stamp(kubernetes_pc, config)
    resource.update(rsp.desired.resources["kubernetes-provider-config"], kubernetes_pc)
    rsp.desired.resources["kubernetes-provider-config"].ready = fnv1.Ready.READY_TRUE
