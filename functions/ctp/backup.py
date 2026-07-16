"""05-backup — GCS Bucket, BackupConfig, RBAC, BackupSchedule.

All resources here are gated on backup.enabled == "yes" (the caller in
main.py handles that gate). The BackupConfig/RBAC Objects are emitted
unconditionally inside that gate — provider-kubernetes Object resources stay
pending until UXP installs the BackupConfig CRD, then reconcile naturally.

Ported from configuration-azure-ctp/backup.py. GCP-specific differences:

* The backup location is a single GCS bucket name (Azure used
  "<storage-account>/<container>").
* A single GCS `Bucket` replaces Azure's StorageAccount + Container pair.
* The Bucket is imported (managementPolicies omit Delete) so the data
  survives ControlPlane deletion.
* There is NO observe-only cluster here: GKE Workload Identity is wired from
  the deterministic workload pool `<project>.svc.id.goog`, so no OIDC issuer
  needs to be read back from the cluster (Azure read oidcIssuerUrl).
* The BackupConfig's objectStorage.provider is "GCP"; with Workload Identity
  the backup controller uses ambient credentials (InjectedIdentity), so no
  per-account `config` block is required (Azure needed config.storage_account).
"""

from crossplane.function import resource

from .prelude import stamp


def add_backup_resources(rsp, id_val, location, project, provider_config,
                         bucket_name, backup, uxp_deployed, config):
    bucket = {
        "apiVersion": "storage.gcp.m.upbound.io/v1beta1",
        "kind": "Bucket",
        "metadata": {
            "name": bucket_name,
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "backup-bucket",
                "crossplane.io/external-name": bucket_name
            }
        },
        "spec": {
            "managementPolicies": ["Observe", "Create", "Update", "LateInitialize"],
            "forProvider": {
                "project": project,
                "location": location,
                "uniformBucketLevelAccess": True,
                "forceDestroy": False
            },
            "providerConfigRef": {
                "name": provider_config,
                "kind": "ProviderConfig"
            }
        }
    }
    stamp(bucket, config)
    resource.update(rsp.desired.resources["backup-bucket"], bucket)

    # BackupConfig — credentials.source: InjectedIdentity uses the Workload
    # Identity token the GKE metadata server projects onto the
    # upbound-controller-manager pod (wired in workload_identity.py). With
    # GCS + Workload Identity the thanos objstore client resolves the bucket
    # from the top-level `bucket` field and authenticates with ambient
    # credentials, so no extra `config` block is needed.
    backup_config = {
        "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
        "kind": "Object",
        "metadata": {
            "name": f"{id_val}-backup-config",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "backup-config"
            }
        },
        "spec": {
            "forProvider": {
                "manifest": {
                    "apiVersion": "admin.uxp.upbound.io/v1beta1",
                    "kind": "BackupConfig",
                    "metadata": {
                        "name": f"{id_val}-backup"
                    },
                    "spec": {
                        "objectStorage": {
                            "provider": "GCP",
                            "bucket": bucket_name,
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
    stamp(backup_config, config)
    resource.update(rsp.desired.resources["backup-config"], backup_config)

    # RBAC: the UXP Helm chart's default ClusterRole for
    # upbound-controller-manager does not grant access to
    # storeconfigs.secrets.crossplane.io. Backup export walks all Crossplane
    # resources including StoreConfigs, so without this extra ClusterRole the
    # backup fails at the export step with a 403.
    backup_rbac = {
        "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
        "kind": "Object",
        "metadata": {
            "name": f"{id_val}-backup-rbac",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "backup-rbac"
            }
        },
        "spec": {
            "forProvider": {
                "manifest": {
                    "apiVersion": "rbac.authorization.k8s.io/v1",
                    "kind": "ClusterRole",
                    "metadata": {
                        "name": "upbound-backup-storeconfigs"
                    },
                    "rules": [
                        {
                            "apiGroups": ["secrets.crossplane.io"],
                            "resources": ["storeconfigs"],
                            "verbs": ["get", "list"]
                        }
                    ]
                }
            },
            "providerConfigRef": {
                "name": id_val,
                "kind": "ProviderConfig"
            }
        }
    }
    stamp(backup_rbac, config)
    resource.update(rsp.desired.resources["backup-rbac"], backup_rbac)

    backup_rbac_binding = {
        "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
        "kind": "Object",
        "metadata": {
            "name": f"{id_val}-backup-rbac-binding",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "backup-rbac-binding"
            }
        },
        "spec": {
            "forProvider": {
                "manifest": {
                    "apiVersion": "rbac.authorization.k8s.io/v1",
                    "kind": "ClusterRoleBinding",
                    "metadata": {
                        "name": "upbound-backup-storeconfigs"
                    },
                    "roleRef": {
                        "apiGroup": "rbac.authorization.k8s.io",
                        "kind": "ClusterRole",
                        "name": "upbound-backup-storeconfigs"
                    },
                    "subjects": [
                        {
                            "kind": "ServiceAccount",
                            "name": "upbound-controller-manager",
                            "namespace": "crossplane-system"
                        }
                    ]
                }
            },
            "providerConfigRef": {
                "name": id_val,
                "kind": "ProviderConfig"
            }
        }
    }
    stamp(backup_rbac_binding, config)
    resource.update(rsp.desired.resources["backup-rbac-binding"], backup_rbac_binding)

    if backup.get("schedule"):
        backup_schedule = {
            "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
            "kind": "Object",
            "metadata": {
                "name": f"{id_val}-backup-schedule",
                "namespace": "default",
                "annotations": {
                    "crossplane.io/composition-resource-name": "backup-schedule"
                }
            },
            "spec": {
                "forProvider": {
                    "manifest": {
                        "apiVersion": "admin.uxp.upbound.io/v1beta1",
                        "kind": "BackupSchedule",
                        "metadata": {
                            "name": f"{id_val}-schedule"
                        },
                        "spec": {
                            "schedule": backup["schedule"],
                            "configRef": {
                                "name": f"{id_val}-backup"
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
        stamp(backup_schedule, config)
        resource.update(rsp.desired.resources["backup-schedule"], backup_schedule)
