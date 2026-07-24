"""
Composition function for GCP GKE Control Plane with UXP backup support.

Ported from configuration-azure-ctp (the most advanced, least buggy sibling).
Each section below is implemented in a sibling module:

  prelude.py            (00) shared extractors and helpers
  network.py            (01) Network XR (configuration-gcp-network)
  gke.py                (02) GKE XR (configuration-gcp-gke)
  uxp.py                (03) UXP v2 Helm Release
  k8gb.py               (04b) k8gb operator + CoreDNS producer
  argo.py               (05b) ArgoCD add-on (UI Gateway/HTTPRoute + app-of-apps)
  usages.py             (04) deletion-order Usage guards
  backup.py             (05) GCS Bucket, BackupConfig, RBAC, Schedule
  workload_identity.py  (06) ServiceAccount, ServiceAccountIAMMember,
                              BucketIAMMember, SA annotation, controller restart, restore
  licensing.py          (07) License Secret + License CR
  vpa.py                (08) VPA Helm Release
  certmanager.py        (09a) always-on cert-manager Helm Release
  gateway.py            (09b) Envoy Gateway data plane (when k8gb or argocd enabled)
  knative.py            (09) knative-operator + serving CR
  runtime_config.py     (10) UpboundRuntimeConfig (ProviderVPA + Knative caps)
  status.py             (99) XR status writeback + ClaimConditions

GKE Workload Identity needs no OIDC-issuer read (the workload pool is the
deterministic <project>.svc.id.goog). Cluster metadata (running node
instanceType) is read from the composed GKE XR's status.gke; the only
observe-only resource composed here is the k8gb CoreDNS Service Object (to read
its LoadBalancer endpoint for the status contract).
"""

from datetime import datetime, timezone

from crossplane.function import resource
from crossplane.function.proto.v1 import run_function_pb2 as fnv1

from .argo import add_argocd_resources
from .backup import add_backup_resources
from .certmanager import add_certmanager_resources
from .gateway import add_gateway_resources
from .gke import add_gke_resources
from .k8gb import add_k8gb_resources
from .knative import add_knative_resources
from .licensing import add_license_resources
from .network import add_network_resources
from .prelude import (
    backup_sa_account_id,
    backup_sa_email,
    build_manager_args,
    check_license_conflict,
    derive_k8gb_ext_geo_tags,
    derive_k8gb_geo_tag,
    get_nodepool_actual_machine_type,
    get_workload_identity_sa_email,
    is_knative_serving_ready,
    is_license_applied,
    is_release_deployed,
    parse_bucket_location,
)
from .runtime_config import add_runtime_config
from .status import update_status
from .usages import add_usage_resources
from .uxp import add_uxp_release
from .vpa import add_vpa_resources
from .workload_identity import add_workload_identity_resources


def compose(req: fnv1.RunFunctionRequest, rsp: fnv1.RunFunctionResponse):
    """Main composition function entry point."""
    config = {
        "last_reconcile_date": datetime.now(timezone.utc).strftime(
            "%A %Y-%m-%d %H:%M:%S UTC"
        ),
    }

    xr = resource.struct_to_dict(req.observed.composite.resource)
    params = xr.get("spec", {}).get("parameters", {})

    # The XR is namespaced (apis/ctp/definition.yaml scope: Namespaced); every
    # composed resource and the sub-XRs' connection secrets co-locate in the XR's
    # own namespace. Falls back to "default" when unset.
    config["namespace"] = xr.get("metadata", {}).get("namespace") or "default"

    id_val = params.get("id", "")
    location = params.get("location", "")
    project = params.get("project", "")
    provider_config = params.get("providerConfigName", "default")
    version = params.get("version", "1.34")
    nodes = params.get("nodes", {})
    backup = params.get("backup", {"enabled": "no"})
    install_from = backup.get("installFrom")
    license_param = params.get("license")
    mgmt_policies = params.get("managementPolicies", ["*"])
    uxp_version = params.get("uxp", {}).get("version", "2.2.1-up.1")
    vpa = params.get("providerVerticalPodAutoscaling")
    knative = params.get("knative")
    k8gb = params.get("k8gb")
    argocd = params.get("argocd")

    k8gb_enabled = bool(k8gb) and k8gb.get("enabled") == "yes"
    argocd_enabled = bool(argocd) and argocd.get("enabled") == "yes"

    # function-extra-resources delivers `allControlPlanes` via the
    # apiextensions.crossplane.io/extra-resources context key.
    context_dict = resource.struct_to_dict(req.context)
    extra_ctx = context_dict.get("apiextensions.crossplane.io/extra-resources", {})
    all_ctps = extra_ctx.get("allControlPlanes", [])

    license_conflict = check_license_conflict(id_val, license_param, all_ctps)

    # k8gb geo tags: this cluster's unique tag, plus same-cloud k8gb peers on
    # the same dnsZone (cross-cloud peers are injected later by FleetGslb).
    k8gb_geo_tag = ""
    k8gb_ext_geo_tags = ""
    if k8gb_enabled:
        k8gb_geo_tag = derive_k8gb_geo_tag(k8gb, location, id_val)
        k8gb_ext_geo_tags = derive_k8gb_ext_geo_tags(
            id_val, k8gb.get("dnsZone", ""), k8gb_geo_tag, all_ctps)

    observed_resources = {
        name: resource.struct_to_dict(res.resource)
        for name, res in req.observed.resources.items()
    }

    # GKE Workload Identity service-account email is deterministic from the
    # project; prefer the observed value once the GSA is created.
    sa_email = ""
    if backup.get("enabled") == "yes" and project:
        sa_email = get_workload_identity_sa_email(observed_resources) or \
            backup_sa_email(backup_sa_account_id(id_val), project)

    uxp_deployed = is_release_deployed(observed_resources, "uxp-release")
    vpa_ready = is_release_deployed(observed_resources, "vpa-release")
    certmanager_ready = is_release_deployed(observed_resources, "certmanager-release")
    gateway_ready = is_release_deployed(observed_resources, "envoy-gateway-release")
    k8gb_deployed = is_release_deployed(observed_resources, "k8gb-release")
    argocd_deployed = is_release_deployed(observed_resources, "argocd-release")
    knative_op_ready = is_release_deployed(observed_resources, "knative-operator-release")
    knative_deps_ready = certmanager_ready and knative_op_ready
    knative_serving_ready = is_knative_serving_ready(observed_resources)
    knative_fully_ready = knative_deps_ready and knative_serving_ready

    license_applied = is_license_applied(observed_resources)
    features_licensed = not license_param or license_applied

    mgr_args = build_manager_args(vpa, knative, vpa_ready, knative_fully_ready, features_licensed)

    bucket_name = parse_bucket_location(backup.get("location", ""))

    ng_actual_machine_type = get_nodepool_actual_machine_type(observed_resources)
    ng_size_mismatch = bool(ng_actual_machine_type) and \
        ng_actual_machine_type != nodes.get("machineType", "")

    # --- Compose resources ---
    add_network_resources(rsp, id_val, location, provider_config,
                          mgmt_policies, config)
    add_gke_resources(rsp, id_val, location, project, provider_config, version,
                      nodes, mgmt_policies, config)
    add_uxp_release(rsp, id_val, uxp_version, uxp_deployed, mgr_args, config)
    add_usage_resources(rsp, id_val, config, k8gb_enabled=k8gb_enabled,
                        argocd_enabled=argocd_enabled)

    # cert-manager is always installed (free component, no license gate) so the
    # k8gb/argocd add-ons can rely on it for Gateway TLS independently of knative.
    add_certmanager_resources(rsp, id_val, certmanager_ready, config)

    # Envoy Gateway is installed only when an add-on needs an HTTP data plane, so
    # plain control planes do not run an idle gateway. Unlike nginx it provisions
    # no cloud LB until a Gateway resource exists.
    if k8gb_enabled or argocd_enabled:
        add_gateway_resources(rsp, id_val, gateway_ready, config)

    # k8gb producer — operator + CoreDNS exposed via a native GCP external LB.
    if k8gb_enabled:
        add_k8gb_resources(rsp, id_val, k8gb, k8gb_geo_tag, k8gb_ext_geo_tags,
                           k8gb_deployed, config)

    if argocd_enabled:
        add_argocd_resources(rsp, id_val, argocd, argocd_deployed,
                             certmanager_ready, gateway_ready, config)

    if backup.get("enabled") == "yes":
        add_backup_resources(rsp, id_val, location, project, provider_config,
                             bucket_name, backup, uxp_deployed, config)

    if backup.get("enabled") == "yes" and uxp_deployed:
        add_workload_identity_resources(rsp, id_val, location, project,
                                        provider_config, bucket_name,
                                        observed_resources, install_from, config)

    if license_param and not license_conflict:
        add_license_resources(rsp, id_val, license_param, config)

    if vpa and vpa.get("enabled") == "yes" and features_licensed:
        add_vpa_resources(rsp, id_val, vpa, vpa_ready, config)

    if knative and knative.get("enabled") == "yes" and features_licensed:
        add_knative_resources(rsp, id_val, knative_op_ready,
                             knative_deps_ready, knative_serving_ready,
                             observed_resources, config)

    if (vpa and vpa.get("enabled") == "yes" and vpa_ready) or \
       (knative and knative.get("enabled") == "yes" and knative_fully_ready):
        add_runtime_config(rsp, id_val, vpa, knative, vpa_ready,
                          knative_fully_ready, config)

    update_status(rsp, id_val, params, uxp_version, uxp_deployed, backup,
                 sa_email, backup.get("location", ""), observed_resources,
                 nodes, ng_actual_machine_type, ng_size_mismatch, vpa, knative,
                 k8gb, k8gb_geo_tag, license_conflict, config)
