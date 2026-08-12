"""E2E test: one real GKE ControlPlane, full stack, asserts Ready=True.

Spins up a real GKE cluster via the composition and exercises the whole stack in
one run: UXP + GKE Workload-Identity backup (GCS) + the k8gb producer (operator +
CoreDNS via a native GCP external Network LB) + ArgoCD (UI Gateway/HTTPRoute +
app-of-apps), which also pulls in cert-manager (always-on) and the Envoy Gateway
data plane.

Credentials: the GCP ProviderConfig "default", namespaced in the ControlPlane's
namespace (platform), uses Upbound-injected identity (source: Upbound) federated
to the shared solutions-e2e GCP service account, so no pre-provisioned Secret is
required. GCP e2e auth is a shared federated identity (not an Azure-style
per-test credential), so the test name is not auth-coupled; the name stays
`controlplane` only for stable reporting.

Asserts the ControlPlane XR reaches Ready=True; function-auto-ready aggregates
every composed resource, so Ready implies UXP + backup chain + Workload Identity
+ every add-on (cert-manager, Envoy Gateway, k8gb + CoreDNS, ArgoCD) came up. The
k8gb CoreDNS endpoint is surfaced on status.controlplane.k8gb.coreDNSEndpoint
(verify non-empty manually - E2ETest cannot assert arbitrary status fields).

Connection-secret placement (manual check - E2ETest asserts conditions, not
Secret existence): confirm the three composition connection secrets land in the
XR namespace ("platform") and none leak into "default":
  kubectl get secret -n platform e2etestcp-gkecluster e2etestcp-sakey e2etestcp-connection
  kubectl get secret -n default | grep e2etestcp   # expect no matches

Scope: installation only. It does NOT test DNS failover - nothing writes the NS
delegation yet (FleetGslb is a separate workstream). No license is supplied; the
backup wiring and add-ons need none (executing a real backup needs a UXP
Standard+ license, which this test does not trigger).

The k8gb CoreDNS LB is a backend-service (RBS) external passthrough NLB serving
TCP+UDP:53 - it needs GKE >= 1.26 (satisfied by version "1.34" below). Requires
GCP quota for a two-node e2-standard-2 GKE cluster in us-central1, a
globally-unique GCS bucket name, plus the add-on load balancers. Expected runtime:
40-70 minutes.
"""

import yaml
from models.io.k8s.apimachinery.pkg.apis.meta import v1 as k8s
from models.io.upbound.dev.meta.e2etest import v1alpha1 as e2etest

test = e2etest.E2ETest(
    metadata=k8s.ObjectMeta(name="controlplane"),
    spec=e2etest.Spec(
        crossplane=e2etest.Crossplane(
            autoUpgrade=e2etest.AutoUpgrade(channel="Stable"),
        ),
        defaultConditions=["Ready"],
        timeoutSeconds=5400,
        cleanupTimeoutSeconds=1800,
        extraResources=[
            {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {"name": "platform"},
            },
            {
                "apiVersion": "gcp.m.upbound.io/v1beta1",
                "kind": "ProviderConfig",
                "metadata": {"name": "default", "namespace": "platform"},
                "spec": {
                    "projectID": "crossplane-playground",
                    "credentials": {
                        "source": "Upbound",
                        "upbound": {
                            "federation": {
                                "providerID": "projects/283222062215/locations/global/workloadIdentityPools/solutions-upbound-oidc-pool/providers/solutions-u5d-oidc-pool",
                                "serviceAccount": "solutions-u5d-service-account@crossplane-playground.iam.gserviceaccount.com",
                            },
                        },
                    },
                },
            },
        ],
        manifests=[
            {
                "apiVersion": "gcp.platform.upbound.io/v1alpha1",
                "kind": "ControlPlane",
                "metadata": {"name": "e2e-test-cp", "namespace": "platform"},
                "spec": {
                    "parameters": {
                        "id": "e2etestcp",
                        "location": "us-central1",
                        "project": "crossplane-playground",
                        "version": "1.34",
                        "nodes": {"count": 2, "machineType": "e2-standard-2"},
                        "backup": {
                            "enabled": "yes",
                            "location": "e2etestcpbackup",
                        },
                        "k8gb": {
                            "enabled": "yes",
                            "dnsZone": "gslb.example.com",
                            "parentZone": "example.com",
                            "strategy": "failover",
                        },
                        "argocd": {
                            "enabled": "yes",
                            "hostname": "argocd.example.com",
                            "url": "https://github.com/argoproj/argocd-example-apps",
                        },
                    },
                },
            },
        ],
        skipDelete=False,
    ),
)

# The test runner expects an "items" array, one entry per test.
item = test.model_dump(by_alias=True, exclude_none=True)
# Strip the two model-default fields the retired test.yaml never carried, so the
# emitted E2ETest is field-for-field identical to it (both equal the platform
# defaults). yaml.dump sorts keys, so only byte order differs; no field is added
# or dropped:
#   spec.crossplane.state == "Running", spec.setupTimeoutSeconds == 600
item["spec"]["crossplane"].pop("state", None)
item["spec"].pop("setupTimeoutSeconds", None)
print(yaml.dump({"items": [item]}))
