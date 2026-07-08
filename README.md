# configuration-gcp-ctp

GCP analog of `configuration-azure-ctp` (the most advanced, least-buggy
sibling in this family — this package was ported from it, not from AWS).
Provisions a GCP GKE cluster configured as an Upbound control plane, installs
UXP, and optionally wires backup (GCS), enterprise license, provider VPA, and
Knative.

Translation of Azure → GCP concepts:

| Azure                                                       | GCP                                                         |
| ----------------------------------------------------------- | ----------------------------------------------------------- |
| ResourceGroup                                               | *(none — resources are scoped to a `project` parameter)*    |
| VirtualNetwork + Subnet                                     | compute Network + Subnetwork (VPC-native, secondary ranges) |
| AKS KubernetesCluster + inline defaultNodePool              | GKE container Cluster + standalone container NodePool        |
| `location`                                                  | `location` (region → regional GKE cluster)                  |
| `vmSize` (Standard_D2s_v3)                                  | `machineType` (e2-standard-2)                               |
| Workload Identity (UserAssignedIdentity + FederatedIdentityCredential + RoleAssignment) | GKE Workload Identity (IAM ServiceAccount + ServiceAccountIAMMember + BucketIAMMember) |
| `azure.workload.identity/client-id` SA anno                 | `iam.gke.io/gcp-service-account` SA anno                    |
| StorageAccount + Container (Blob)                           | GCS Bucket                                                  |
| backup.location `<account>/<container>`                     | backup.location `<bucket>` (single global name)             |
| OIDC issuer URL read from cluster status                    | *(none — workload pool is the deterministic `<project>.svc.id.goog`)* |

## Key GCP differences from the Azure sibling

* **`project` is a required parameter.** GCP has no ResourceGroup; every
  resource is scoped to a project. `forProvider.project` is set on the
  Network, Subnetwork, Cluster, NodePool, Bucket, and IAM ServiceAccount.
* **Workload Identity needs no OIDC-issuer read.** The GKE workload pool is
  deterministic (`<project>.svc.id.goog`), so there is no observe-only cluster
  and no `FederatedIdentityCredential`. The KSA→GSA binding is a
  `ServiceAccountIAMMember` with `roles/iam.workloadIdentityUser`; the GSA gets
  `roles/storage.objectAdmin` on the bucket via a `BucketIAMMember`.
* **The worker node pool is a standalone managed resource.** The cluster is
  created with `removeDefaultNodePool: true` + `initialNodeCount: 1` (the
  idiomatic upjet/Terraform GKE pattern), and a separate `container.NodePool`
  holds the real workers. The node pool runs `workloadMetadataConfig.mode:
  GKE_METADATA` so Workload Identity functions.
* **`deletionProtection: false`** is set on the cluster (upjet GKE clusters
  default it to true, which blocks deletion).

The cross-cutting UXP fixes that the Azure sibling discovered (License CR
apiVersion/name/secret field path, UpboundRuntimeConfig apiVersion,
cert-manager namespace creation, knative-serving namespace, provider-helm
stale-Ready workaround, knative-operator install-once policy) are inherited
verbatim — `licensing.py`, `runtime_config.py`, `knative.py`, and `uxp.py` are
byte-for-byte copies of the Azure modules.

## ⚠️ Validation status — shapes derived from documented conventions

This package was authored from documented provider-gcp conventions, **not**
from a local CRD schema cache (Claude has no GCP credentials and could not
introspect the installed CRDs). The following are the highest-risk areas to
verify with `up project build` and a real E2E run before trusting them:

* **v2 namespaced MR API groups/versions** — e.g.
  `container.gcp.m.upbound.io/v1beta2` (Cluster, NodePool),
  `compute.gcp.m.upbound.io/v1beta1` (Network, Subnetwork),
  `storage.gcp.m.upbound.io/v1beta1` (Bucket, BucketIAMMember),
  `cloudplatform.gcp.m.upbound.io/v1beta1` (ServiceAccount,
  ServiceAccountIAMMember). Confirm each against the installed CRD:

  ```bash
  kubectl get crd clusters.container.gcp.m.upbound.io \
    -o jsonpath='{.spec.versions[*].name}'
  ```

* **GKE kubeconfig delivery.** The composition writes the cluster's kubeconfig
  to a connection secret and points the Helm/Kubernetes ProviderConfigs at the
  `kubeconfig` key, mirroring AKS. Verify upjet-gcp's GKE Cluster actually
  publishes a directly-usable `kubeconfig` connection detail; if it doesn't,
  UXP install on the inner cluster will need an alternative auth path.
* **GKE version enum.** `version` is used as the cluster's `minMasterVersion`.
  Available versions vary by region/channel — check before pinning a default:

  ```bash
  gcloud container get-server-config --region us-central1
  ```

* **BackupConfig GCS config keys.** `objectStorage` is emitted as
  `provider: GCS`, `bucket: <name>`, `credentials.source: InjectedIdentity`
  (no per-account `config` block — GCS + Workload Identity uses ambient creds).
  Confirm against thanos objstore's GCS config expectations.

## Installation

TODO — push to a registry and install the Configuration package on a control
plane.

### Upper (management) cluster: configure the GCP provider

The management cluster running this package needs a GCP `ClusterProviderConfig`
so the providers can authenticate. See `examples/install/` for three flavors:

* `gcp-providerconfig-secret.yaml` — service-account key in a Secret (use this
  on local KIND / any cluster without a trusted OIDC issuer).
* `gcp-providerconfig-upbound.yaml` — Upbound's federated OIDC broker (use this
  when the management plane runs on Upbound Cloud Spaces).
* `gcp-providerconfig-oidc.yaml` — `InjectedIdentity` (use this when the
  management plane is self-hosted UXP on GKE with Workload Identity wired).

Gather the values you'll need:

```bash
# Project + a service account for the Secret flavor (long-lived key, dev-only)
PROJECT=<your-project-id>
gcloud iam service-accounts create upbound-gcp-ctp \
  --project "$PROJECT" --display-name "Upbound GCP CTP"
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member "serviceAccount:upbound-gcp-ctp@${PROJECT}.iam.gserviceaccount.com" \
  --role roles/owner
gcloud iam service-accounts keys create key.json \
  --iam-account "upbound-gcp-ctp@${PROJECT}.iam.gserviceaccount.com"
# Paste key.json into examples/install/gcp-credentials.yaml (under stringData.credentials).
```

Apply the chosen ProviderConfig (and the credentials secret if you used the
Secret flavor) once, then proceed to applying `ControlPlane` XRs.

### Required GCP permissions

This package creates resources spanning compute, container, storage, and IAM.
When `backup.enabled: "yes"`, the principal that authenticates the providers
also creates an IAM ServiceAccount and IAM policy bindings:

| Operation | Role required |
| --- | --- |
| Create Network, Subnetwork, GKE Cluster/NodePool, GCS Bucket | `roles/editor` (or narrower compute/container/storage admin roles) |
| Create the backup IAM ServiceAccount | `roles/iam.serviceAccountAdmin` |
| Create the Workload Identity + bucket IAM bindings (`backup.enabled: yes`) | `roles/resourcemanager.projectIamAdmin` (+ bucket-level IAM) |

`roles/editor` alone does **not** include `iam.serviceAccountAdmin` or
`resourcemanager.projectIamAdmin`. The simplest setup grants `roles/owner`.

**Symptom if IAM is insufficient:** the `ServiceAccount` /
`ServiceAccountIAMMember` / `BucketIAMMember` MRs sit at `Synced=False` with a
`PermissionDenied` event. **You can skip these roles entirely** if
`spec.parameters.backup.enabled` stays at the default `"no"` — the IAM
resources are gated on backup being on, so `roles/editor` suffices for the rest
of the composition.

### UXP enterprise license: apply ONLY as a Secret on the management cluster

When `spec.parameters.license.secretRef` is set, the composition copies the
license payload from a Secret on the management (upper) cluster into a Secret
on the newly-created downstream (inner) GKE cluster, then creates a `License`
CR there. Apply the license JSON ONLY as a Kubernetes Secret on the management
cluster — one Secret can serve any number of `ControlPlane` XRs:

```bash
kubectl create secret generic uxp-license \
  --from-file=license.json=./license.json \
  -n crossplane-system \
  --dry-run=client -o yaml | kubectl apply -f -
```

Then each `ControlPlane` XR references it:

```yaml
spec:
  parameters:
    license:
      secretRef:
        name: uxp-license
        namespace: crossplane-system
```

**Do NOT run `up uxp license apply <license.json>` on the management cluster**
when the license is intended for a downstream control plane — it creates a
(useless, cosmetic-error-producing) management-side License CR in addition to
the Secret. If you already did, `kubectl delete license.licensing.upbound.io/uxp`
is safe.

#### License-validity gating

The license's embedded `restrictions.clusterType` (e.g. `SingleNodeKind` for
dev licenses) is checked on the **downstream cluster** during validation. A
dev license restricted to single-node Kind clusters will fail validation on a
multi-node GKE regardless of `nodes.count`. Inspect the embedded claims:

```bash
kubectl get secret -n crossplane-system uxp-license \
  -o jsonpath='{.data.license\.json}' | base64 -d | python3 -m json.tool
```

Look for `plan` and `restrictions.clusterType`.

## Inner (GKE) cluster: what you get out of the box

When this composition provisions a GKE cluster, the cluster is configured with:

* `workloadIdentityConfig.workloadPool: <project>.svc.id.goog` and a node pool
  running `workloadMetadataConfig.mode: GKE_METADATA` — so any pod whose KSA is
  annotated with `iam.gke.io/gcp-service-account` impersonates the bound GSA.
* UXP installed in `crossplane-system`. UXP's backup controller already uses
  Workload Identity (wired by `functions/ctp/workload_identity.py`) when
  `spec.parameters.backup.enabled: "yes"`.

This composition deliberately does **not** install GCP providers or a generic
GCP `ProviderConfig` inside the inner cluster: a control plane created with
this package may manage resources in GCP, AWS, Azure, on-prem, or any mix.
Everything needed to wire Workload Identity for any future provider — the
workload pool, the metadata server, the IAM primitives — is already present.

To wire a follow-on provider's KSA to a GSA on the inner cluster:

```bash
PROJECT=<project>
gcloud iam service-accounts add-iam-policy-binding \
  <gsa>@${PROJECT}.iam.gserviceaccount.com \
  --role roles/iam.workloadIdentityUser \
  --member "serviceAccount:${PROJECT}.svc.id.goog[upbound-system/<provider-sa>]"
kubectl annotate sa -n upbound-system <provider-sa> \
  iam.gke.io/gcp-service-account=<gsa>@${PROJECT}.iam.gserviceaccount.com
```

## Examples

See `examples/controlplane/`:

* `basic.yaml` — minimal GKE control plane with UXP
* `with-backup.yaml` — backup enabled, schedule, VPA, Knative, license
* `uxp-ctp-1.yaml` — opinionated production-style example

All require `spec.parameters.project` to be set to a real GCP project ID.

## Testing

* Composition tests: `up test run tests/test-controlplane`
* E2E (real GCP): `up test run tests/e2etest-controlplane --e2e`

The E2E test requires a working GCP ClusterProviderConfig named `default` with
sufficient permissions to create networks, GKE clusters, service accounts, IAM
bindings, and GCS buckets. 

> Note: the composition tests are loose shape asserts — they verify field
> names, not that apiVersions/field paths are correct against the live CRDs.
> Run `up project build` (schema validation) and a real E2E before trusting the
> GCP MR shapes flagged above.

## Status

Not production-ready until composition tests, build, and E2E tests pass.
The GCP provider managed-resource shapes in this
package were derived from documented provider conventions rather than from a
local schema cache, so `up project build` is the first thing to run.
