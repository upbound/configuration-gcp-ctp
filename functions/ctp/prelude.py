"""
00-prelude — shared extractors and helpers.

Read-only logic that inspects parameters and observed state to derive values
consumed by every other section. Ported from configuration-azure-ctp's
prelude.py.

Key GCP differences from the Azure sibling:

* GKE Workload Identity does NOT require reading an OIDC issuer URL back from
  the cluster. The workload-identity pool is deterministic from the project:
  ``<project>.svc.id.goog``. There is therefore no ``extract_oidc_info`` here.
* GCP IAM members are plain strings (``serviceAccount:<email>`` /
  ``serviceAccount:<pool>[<ns>/<ksa>]``) built from the project + service
  account email, so there is no need to read a principalId / scope back from
  observed state the way the Azure RoleAssignment did.
* GCP resource ``labels`` only permit lowercase ``[a-z0-9_-]`` values, so the
  human-readable reconcile timestamp is written ONLY as a metadata annotation
  (never into ``spec.forProvider.labels``).
"""

import re
from typing import Dict, List, Optional

# Kubernetes ServiceAccount the UXP backup controller runs as on the inner
# (GKE) control plane.
CONTROLLER_NAMESPACE = "crossplane-system"
CONTROLLER_KSA = "upbound-controller-manager"


def stamp(resource_dict: dict, config: Dict) -> None:
    """Stamp a resource with the current reconciliation timestamp.

    Every resource carries ``last-reconcile-date`` as a metadata annotation so
    an operator can see when this composition function last touched it. Unlike
    the Azure sibling (which also wrote the timestamp into Azure ``tags``), GCP
    resource ``labels`` only allow lowercase ``[a-z0-9_-]`` values — the
    human-readable timestamp is not a valid label value — so the timestamp is
    recorded as an annotation only.
    """
    meta = resource_dict.setdefault("metadata", {})
    ann = meta.setdefault("annotations", {})
    ann["last-reconcile-date"] = config["last_reconcile_date"]


def check_license_conflict(id_val: str, license_param: Optional[Dict],
                           all_ctps: List[Dict]) -> str:
    """Return the name of another ControlPlane that already claims the same
    license secret (namespace/name pair), or "" if there is no conflict."""
    if not license_param or not all_ctps:
        return ""

    my_ns = license_param.get("secretRef", {}).get("namespace", "default")
    my_name = license_param.get("secretRef", {}).get("name", "")
    my_key = f"{my_ns}/{my_name}"

    for ctp in all_ctps:
        c_name = ctp.get("metadata", {}).get("name", "")
        if c_name and c_name != id_val:
            c_license = ctp.get("spec", {}).get("parameters", {}).get("license", {})
            if c_license and c_license.get("secretRef"):
                c_ns = c_license["secretRef"].get("namespace", "default")
                c_name2 = c_license["secretRef"].get("name", "")
                c_key = f"{c_ns}/{c_name2}"
                if c_name2 and c_key == my_key:
                    return c_name
    return ""


def workload_identity_pool(project: str) -> str:
    """Return the GKE workload-identity pool for a project: ``<project>.svc.id.goog``."""
    return f"{project}.svc.id.goog"


def workload_identity_member(project: str, namespace: str, ksa: str) -> str:
    """Return the IAM member string that maps a Kubernetes ServiceAccount to a
    Google ServiceAccount via GKE Workload Identity."""
    return f"serviceAccount:{project}.svc.id.goog[{namespace}/{ksa}]"


def backup_sa_account_id(id_val: str) -> str:
    """Derive a valid Google ServiceAccount accountId for the backup identity.

    GCP requires accountId to be 6–30 chars, lowercase letters/digits/hyphens,
    starting with a letter. Sanitize the ControlPlane id and append a suffix.
    """
    base = re.sub(r"[^a-z0-9-]", "-", id_val.lower()).strip("-")
    if not base or not base[0].isalpha():
        base = f"g{base}"
    # Reserve room for the "-backup" suffix (7 chars) within the 30-char cap.
    base = base[:23].rstrip("-")
    return f"{base}-backup"


def backup_sa_email(account_id: str, project: str) -> str:
    """Return the deterministic email of a Google ServiceAccount."""
    return f"{account_id}@{project}.iam.gserviceaccount.com"


def get_workload_identity_sa_email(observed: Dict) -> str:
    """Return the email of the observed backup Google ServiceAccount, or "" if
    not yet synced. provider-gcp-cloudplatform exposes it at
    status.atProvider.email once the SA is created."""
    obs = observed.get("backup-identity")
    if not obs:
        return ""

    res = obs.resource if hasattr(obs, "resource") else obs
    return res.get("status", {}).get("atProvider", {}).get("email", "")


def get_cluster_name(id_val: str, observed: Dict) -> str:
    """Return the GKE cluster name from the composed GKE XR's
    status.gke.clusterName, or id_val until the XR surfaces it
    (configuration-gcp-gke v2.0.3+)."""
    obs = observed.get("gke")
    if not obs:
        return id_val

    res = obs.resource if hasattr(obs, "resource") else obs
    return res.get("status", {}).get("gke", {}).get("clusterName", "") or id_val


def is_release_deployed(observed: Dict, name: str) -> bool:
    """True when the observed Helm Release has atProvider.state == 'deployed'."""
    obs = observed.get(name)
    if not obs:
        return False

    res = obs.resource if hasattr(obs, "resource") else obs
    state = res.get("status", {}).get("atProvider", {}).get("state", "")
    return state == "deployed"


def is_knative_serving_ready(observed: Dict) -> bool:
    """True when the KnativeServing CR reports Ready=True in its embedded
    manifest status (provider-kubernetes Object)."""
    obs = observed.get("knative-serving-cr")
    if not obs:
        return False

    res = obs.resource if hasattr(obs, "resource") else obs
    manifest_status = res.get("status", {}).get("atProvider", {}).get("manifest", {}).get("status", {})
    for cond in manifest_status.get("conditions", []):
        if cond.get("type") == "Ready" and cond.get("status") == "True":
            return True
    return False


def is_license_applied(observed: Dict) -> bool:
    """True when the License Object reports Ready=True (license accepted)."""
    obs = observed.get("uxp-license")
    if not obs:
        return False

    res = obs.resource if hasattr(obs, "resource") else obs
    for cond in res.get("status", {}).get("conditions", []):
        if cond.get("type") == "Ready" and cond.get("status") == "True":
            return True
    return False


def build_manager_args(vpa: Optional[Dict], knative: Optional[Dict],
                       vpa_ready: bool, knative_ready: bool,
                       features_licensed: bool) -> List[str]:
    """Assemble the upbound.manager.args list for the UXP Helm Release based on
    which optional features are enabled, deployed, and licensed."""
    args: List[str] = []

    if vpa and vpa.get("enabled") == "yes" and vpa_ready and features_licensed:
        args.append("--enable-provider-vpa")

    if knative and knative.get("enabled") == "yes" and knative_ready and features_licensed:
        args.append("--enable-knative-runtime")

    return args


def parse_bucket_location(location: str) -> str:
    """Validate and return a GCS bucket name from a backup location.

    Unlike Azure (which used "<storage-account>/<container>"), a GCS bucket is
    a single global namespace, so the backup location is just the bucket name.
    Returns "" for malformed input.
    """
    if not location:
        return ""
    if re.match(r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$", location):
        return location
    return ""


def get_nodepool_actual_machine_type(observed: Dict) -> str:
    """Return the running node instanceType from the composed GKE XR's
    status.gke.nodePool.instanceType, or "" until the XR surfaces it
    (configuration-gcp-gke v2.0.3+)."""
    obs = observed.get("gke")
    if not obs:
        return ""

    res = obs.resource if hasattr(obs, "resource") else obs
    return res.get("status", {}).get("gke", {}).get("nodePool", {}).get("instanceType", "")


def derive_k8gb_geo_tag(k8gb_param: Optional[Dict], location: str,
                        id_val: str) -> str:
    """The k8gb clusterGeoTag, unique per control plane. Defaults to
    gcp-<location>-<id> (a bare gcp-<location> collides when two CPs share a
    location); an explicit clusterGeoTag param overrides it."""
    tag = (k8gb_param or {}).get("clusterGeoTag")
    return tag if tag else f"gcp-{location}-{id_val}"


def derive_k8gb_ext_geo_tags(id_val: str, dns_zone: str, my_tag: str,
                             all_ctps: List[Dict]) -> str:
    """Comma-separated geo tags of same-cloud peer control planes that have
    k8gb enabled on the same dnsZone. Cross-cloud peers are injected by the
    fleet layer (FleetGslb) and are out of scope here; empty is fine for a
    single-cluster start."""
    tags = set()
    for ctp in all_ctps:
        c_name = ctp.get("metadata", {}).get("name", "")
        if not c_name or c_name == id_val:
            continue
        c_params = ctp.get("spec", {}).get("parameters", {})
        c_k8gb = c_params.get("k8gb", {}) or {}
        if c_k8gb.get("enabled") != "yes" or c_k8gb.get("dnsZone") != dns_zone:
            continue
        c_tag = derive_k8gb_geo_tag(c_k8gb, c_params.get("location", ""), c_name)
        if c_tag and c_tag != my_tag:
            tags.add(c_tag)
    return ",".join(sorted(tags))


def extract_coredns_endpoint(observed: Dict) -> str:
    """The k8gb CoreDNS LoadBalancer endpoint (IP or hostname) read from the
    observe-only Object on the child CoreDNS Service. Empty until the LB is
    provisioned. On GCP this is an IP, directly usable as NS glue."""
    obs = observed.get("k8gb-coredns-observe")
    if not obs:
        return ""
    res = obs.resource if hasattr(obs, "resource") else obs
    ingress = (res.get("status", {})
                  .get("atProvider", {})
                  .get("manifest", {})
                  .get("status", {})
                  .get("loadBalancer", {})
                  .get("ingress", []))
    if not ingress:
        return ""
    first = ingress[0]
    return first.get("ip") or first.get("hostname") or ""
