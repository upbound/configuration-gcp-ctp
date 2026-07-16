# Install examples

These manifests configure the GCP provider on the upper (management) cluster
where the `configuration-gcp-ctp` package is installed. Pick one of the three
credential flavors and apply it once before applying a `ControlPlane` XR.

| File                              | Source                  | When to use                                                                 |
| --------------------------------- | ----------------------- | --------------------------------------------------------------------------- |
| `gcp-providerconfig-secret.yaml`    | `Secret`              | Quickest to set up. Long-lived service-account key. Local dev / KIND.       |
| `gcp-providerconfig-upbound.yaml`   | `Upbound` (OIDC)      | **Recommended** for Upbound Cloud Spaces control planes — federated, keyless. |
| `gcp-providerconfig-oidc.yaml`      | `InjectedIdentity`    | Self-hosted UXP on a GKE management cluster with Workload Identity wired.    |

All three install a namespaced `ProviderConfig` named `default` in the
`default` namespace, which matches the default `providerConfigName` on the
`ControlPlane` XR and the `default` namespace the composed managed resources
are placed in. Override `spec.parameters.providerConfigName` on the XR to use a
different name. Every flavor must set `spec.projectID` to the GCP project the
providers act in.

## Secret-based setup

```bash
cp gcp-credentials.yaml.example gcp-credentials.yaml
# edit gcp-credentials.yaml with your service account key JSON
kubectl apply -f gcp-credentials.yaml
# edit gcp-providerconfig-secret.yaml with your projectID
kubectl apply -f gcp-providerconfig-secret.yaml
```

## Upbound federated OIDC setup

One-time GCP-side setup (per Upbound organization):

1. Create a Workload Identity Pool + OIDC Provider whose issuer is
   `https://proidc.upbound.io`.
2. Create (or reuse) a Google ServiceAccount and grant it the project roles it
   needs (see "Required GCP permissions" below).
3. Grant the federated principal `roles/iam.workloadIdentityUser` on that
   Google ServiceAccount.
4. Put the project ID and the service-account email into
   `gcp-providerconfig-upbound.yaml` and apply. See
   [Upbound's managed-identities docs][upbound-mi].

[upbound-mi]: https://docs.upbound.io/concepts/control-planes/configuration/managed-identities/

## InjectedIdentity (GKE Workload Identity) setup

Used for self-hosted UXP on a GKE management cluster. See the comments in
`gcp-providerconfig-oidc.yaml` — you must pre-wire Workload Identity for the
provider pods on the upper cluster yourself.

## Required GCP permissions

The principal that authenticates the providers needs more than the default
`Editor` role when `backup.enabled: "yes"`, because the composition creates an
IAM ServiceAccount and IAM policy bindings on the inner control plane:

| Operation | Role required |
| --- | --- |
| Create Network, Subnetwork, GKE Cluster/NodePool, GCS Bucket | `roles/editor` (or narrower compute/container/storage admin roles) |
| Create the backup IAM ServiceAccount | `roles/iam.serviceAccountAdmin` |
| Create the Workload Identity + bucket IAM bindings (`backup.enabled: yes`) | `roles/resourcemanager.projectIamAdmin` (+ bucket-level IAM) |

The simplest setup grants `roles/owner` on the project. **You can skip the IAM
roles entirely** if `spec.parameters.backup.enabled` is left at the default
`"no"`: the IAM ServiceAccount and bindings are gated on backup being on.

## Note on the GKE cluster's own Workload Identity

This configuration *creates* GKE clusters with
`workloadIdentityConfig.workloadPool: <project>.svc.id.goog` and node pools
running `workloadMetadataConfig.mode: GKE_METADATA`, then wires a Google
ServiceAccount + IAM bindings for the UXP backup controller on the new cluster
(see `functions/ctp/workload_identity.py`). That is independent of the
ProviderConfig manifests in this directory, which authenticate the *upper*
cluster's provider pods to GCP.
