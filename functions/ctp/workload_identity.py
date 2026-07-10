"""06-workload-identity — Google ServiceAccount + ServiceAccountIAMMember +
BucketIAMMember + Kubernetes ServiceAccount annotation + controller restart +
optional Restore-from-backup.

GKE Workload Identity. Gated by the caller on backup.enabled == "yes" and UXP
deployed — see compose() in main.py. Ported from
configuration-azure-ctp/workload_identity.py.

Where Azure Workload Identity used a UserAssignedIdentity +
FederatedIdentityCredential (bound to the AKS OIDC issuer) + RoleAssignment,
GKE Workload Identity uses:

  ServiceAccount (GSA)        — the Google identity the KSA impersonates
  ServiceAccountIAMMember     — grants the KSA the workloadIdentityUser role
                                on the GSA via the deterministic member
                                serviceAccount:<project>.svc.id.goog[ns/ksa]
  BucketIAMMember             — grants the GSA objectAdmin on the backup
                                bucket so the controller can read/write objects

The Kubernetes ServiceAccount is then annotated with
`iam.gke.io/gcp-service-account: <gsa-email>` and the controller deployment is
rolled to pick up the federated-token projection from the GKE metadata server.
Unlike Azure, no FederatedIdentityCredential and no OIDC-issuer URL are needed
— the workload pool `<project>.svc.id.goog` is deterministic, and the node
pool already runs with workloadMetadataConfig.mode=GKE_METADATA (see gke.py).
"""

from crossplane.function import resource

from .prelude import (
    CONTROLLER_KSA,
    CONTROLLER_NAMESPACE,
    backup_sa_account_id,
    backup_sa_email,
    parse_bucket_location,
    stamp,
    workload_identity_member,
)


def add_workload_identity_resources(rsp, id_val, location, project,
                                    provider_config, bucket_name, observed,
                                    install_from, config):
    account_id = backup_sa_account_id(id_val)
    sa_email = backup_sa_email(account_id, project)

    # Google ServiceAccount — the GSA the backup controller KSA impersonates.
    identity = {
        "apiVersion": "cloudplatform.gcp.m.upbound.io/v1beta1",
        "kind": "ServiceAccount",
        "metadata": {
            "name": f"{id_val}-backup-identity",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "backup-identity",
                "crossplane.io/external-name": account_id
            }
        },
        "spec": {
            "forProvider": {
                "project": project,
                "displayName": f"UXP backup identity for {id_val}"
            },
            "providerConfigRef": {
                "name": provider_config,
                "kind": "ClusterProviderConfig"
            }
        }
    }
    stamp(identity, config)
    resource.update(rsp.desired.resources["backup-identity"], identity)

    # Bind the UXP controller KSA to the GSA via Workload Identity. The member
    # string is deterministic from the project + KSA, so no observed state is
    # required.
    wi_binding = {
        "apiVersion": "cloudplatform.gcp.m.upbound.io/v1beta1",
        "kind": "ServiceAccountIAMMember",
        "metadata": {
            "name": f"{id_val}-backup-wi-binding",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "backup-wi-binding"
            }
        },
        "spec": {
            "forProvider": {
                "serviceAccountIdRef": {
                    "name": f"{id_val}-backup-identity"
                },
                "role": "roles/iam.workloadIdentityUser",
                "member": workload_identity_member(
                    project, CONTROLLER_NAMESPACE, CONTROLLER_KSA
                )
            },
            "providerConfigRef": {
                "name": provider_config,
                "kind": "ClusterProviderConfig"
            }
        }
    }
    stamp(wi_binding, config)
    resource.update(rsp.desired.resources["backup-wi-binding"], wi_binding)

    # Grant the GSA object admin on the backup bucket so the controller can
    # list/read/write backup objects.
    bucket_binding = {
        "apiVersion": "storage.gcp.m.upbound.io/v1beta1",
        "kind": "BucketIAMMember",
        "metadata": {
            "name": f"{id_val}-backup-bucket-role",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "backup-bucket-role"
            }
        },
        "spec": {
            "forProvider": {
                "bucketRef": {
                    "name": bucket_name
                },
                "role": "roles/storage.objectAdmin",
                "member": f"serviceAccount:{sa_email}"
            },
            "providerConfigRef": {
                "name": provider_config,
                "kind": "ClusterProviderConfig"
            }
        }
    }
    stamp(bucket_binding, config)
    resource.update(rsp.desired.resources["backup-bucket-role"], bucket_binding)

    # Annotate the UXP ServiceAccount with the GSA email; the next pod restart
    # picks up the federated-token projection from the GKE metadata server.
    sa_patch = {
        "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
        "kind": "Object",
        "metadata": {
            "name": f"{id_val}-backup-sa",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "backup-sa"
            }
        },
        "spec": {
            "forProvider": {
                "manifest": {
                    "apiVersion": "v1",
                    "kind": "ServiceAccount",
                    "metadata": {
                        "name": CONTROLLER_KSA,
                        "namespace": CONTROLLER_NAMESPACE,
                        "annotations": {
                            "iam.gke.io/gcp-service-account": sa_email
                        }
                    }
                }
            },
            "providerConfigRef": {
                "name": id_val,
                "kind": "ProviderConfig"
            }
        }
    }
    stamp(sa_patch, config)
    resource.update(rsp.desired.resources["backup-sa"], sa_patch)

    # Rolling restart of the controller deployment so it picks up the new SA
    # projection. The literal restartedAt value is written once, which forces
    # a single rollout when the Object is first applied.
    controller_restart = {
        "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
        "kind": "Object",
        "metadata": {
            "name": f"{id_val}-controller-restart",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "controller-restart"
            }
        },
        "spec": {
            "forProvider": {
                "manifest": {
                    "apiVersion": "apps/v1",
                    "kind": "Deployment",
                    "metadata": {
                        "name": CONTROLLER_KSA,
                        "namespace": CONTROLLER_NAMESPACE,
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt": "{{ now }}"
                        }
                    }
                }
            },
            "providerConfigRef": {
                "name": id_val,
                "kind": "ProviderConfig"
            }
        }
    }
    stamp(controller_restart, config)
    resource.update(rsp.desired.resources["controller-restart"], controller_restart)

    if install_from:
        src_bucket = parse_bucket_location(install_from.get("location", ""))
        restore_name = install_from.get("name", "")

        if src_bucket and restore_name:
            restore = {
                "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
                "kind": "Object",
                "metadata": {
                    "name": f"{id_val}-backup-restore",
                    "namespace": "default",
                    "annotations": {
                        "crossplane.io/composition-resource-name": "backup-restore"
                    }
                },
                "spec": {
                    "forProvider": {
                        "manifest": {
                            "apiVersion": "admin.uxp.upbound.io/v1beta1",
                            "kind": "Restore",
                            "metadata": {
                                "name": f"{id_val}-restore"
                            },
                            "spec": {
                                "backupRef": {
                                    "name": restore_name
                                },
                                "backupLocation": {
                                    "provider": "GCP",
                                    "bucket": src_bucket,
                                    "credentials": {
                                        "source": "InjectedIdentity"
                                    }
                                }
                            }
                        }
                    },
                    "providerConfigRef": {
                        "name": id_val,
                        "kind": "ProviderConfig"
                    }
                }
            }
            stamp(restore, config)
            resource.update(rsp.desired.resources["backup-restore"], restore)
