"""99-status — XR status writeback + ClaimConditions.

Aggregates composed-resource readiness, derives feature flags, and surfaces
operator-facing conditions (Ready, NodePoolMachineTypeImmutable,
LicenseConflict). Ported from configuration-azure-ctp/status.py.
"""

from crossplane.function import resource

from .prelude import extract_coredns_endpoint


def update_status(rsp, id_val, params, uxp_version, uxp_deployed, backup,
                 sa_email, bucket_ref, observed, nodes, ng_actual_machine_type,
                 ng_size_mismatch, vpa, knative, k8gb, k8gb_geo_tag,
                 license_conflict, config):
    # rsp.desired.composite.resource is a google.protobuf.Struct — convert
    # so we can read fields out of the partially-built XR.
    xr_dict = resource.struct_to_dict(rsp.desired.composite.resource)
    creation_time = xr_dict.get("metadata", {}).get("creationTimestamp", "")

    status = {
        "controlplane": {
            "created": creation_time,
            "lastReconcileDate": config["last_reconcile_date"],
            "uxp": {
                "version": uxp_version,
                "ready": uxp_deployed
            }
        }
    }

    if backup.get("enabled") == "yes":
        backup_status = {
            "enabled": "yes",
            "bucketRef": bucket_ref
        }
        if sa_email:
            backup_status["serviceAccountEmail"] = sa_email

        schedule_obs = observed.get("backup-schedule")
        if schedule_obs:
            res = schedule_obs.resource if hasattr(schedule_obs, "resource") else schedule_obs
            last_backup = (
                res.get("status", {})
                   .get("atProvider", {})
                   .get("manifest", {})
                   .get("status", {})
                   .get("lastBackupTime")
            )
            if last_backup:
                backup_status["lastBackupTime"] = last_backup

        status["controlplane"]["backup"] = backup_status

    total = 0
    synced = 0
    ready = 0
    synced_and_ready = 0

    for _name, obs_res in observed.items():
        res = obs_res.resource if hasattr(obs_res, "resource") else obs_res
        is_synced = False
        is_ready = False
        for cond in res.get("status", {}).get("conditions", []):
            if cond.get("type") == "Synced" and cond.get("status") == "True":
                is_synced = True
            if cond.get("type") == "Ready" and cond.get("status") == "True":
                is_ready = True

        total += 1
        if is_synced:
            synced += 1
        if is_ready:
            ready += 1
        if is_synced and is_ready:
            synced_and_ready += 1

    status["controlplane"]["resources"] = {
        "total": total,
        "synced": synced,
        "ready": ready,
        "syncedAndReady": synced_and_ready
    }

    status["controlplane"]["nodes"] = {
        "machineType": nodes.get("machineType", "")
    }
    if ng_actual_machine_type:
        status["controlplane"]["nodes"]["currentMachineType"] = ng_actual_machine_type

    if vpa:
        status["controlplane"]["providerVerticalPodAutoscaling"] = {
            "enabled": vpa.get("enabled", "no")
        }

    if knative:
        status["controlplane"]["knative"] = {
            "enabled": knative.get("enabled", "no")
        }

    if k8gb:
        k8gb_status = {"enabled": k8gb.get("enabled", "no")}
        if k8gb.get("enabled") == "yes":
            endpoint = extract_coredns_endpoint(observed)
            if endpoint:
                dns_zone = k8gb.get("dnsZone", "")
                parent_zone = k8gb.get("parentZone", "")
                # k8gb getNsName (byte-identical v0.15.0..v0.20.0): strip the
                # ".<parentZone>" suffix from the load-balanced zone, replace the
                # remaining dots with dashes, and place the geo tag BEFORE the
                # domain component. This is the contract FleetGslb consumes.
                zone_label = dns_zone
                suffix = f".{parent_zone}"
                if zone_label.endswith(suffix):
                    zone_label = zone_label[: -len(suffix)]
                zone_label = zone_label.replace(".", "-")
                ns_name = f"gslb-ns-{k8gb_geo_tag}-{zone_label}.{parent_zone}"
                k8gb_status["coreDNSEndpoint"] = endpoint
                k8gb_status["delegationRecord"] = (
                    f"{dns_zone}. NS {ns_name}. ; {ns_name}. A {endpoint}"
                )
        status["controlplane"]["k8gb"] = k8gb_status

    conditions = []

    if synced_and_ready == total and total > 0:
        conditions.append({
            "type": "Ready",
            "status": "True",
            "reason": "Available",
            "message": "Control plane is ready"
        })
    else:
        conditions.append({
            "type": "Ready",
            "status": "False",
            "reason": "Creating",
            "message": f"Waiting for resources: {synced_and_ready}/{total} ready"
        })

    if ng_size_mismatch:
        conditions.append({
            "type": "NodePoolMachineTypeImmutable",
            "status": "True",
            "reason": "ImmutableField",
            "message": (
                f"GKE node pool machineType cannot be changed in place. "
                f"Current: {ng_actual_machine_type}, "
                f"Desired: {nodes.get('machineType')}. Changing machineType "
                "recreates the node pool; to migrate without disruption, "
                "provision a new ControlPlane with backup.installFrom pointing "
                "to this control plane's backup."
            )
        })

    if license_conflict:
        conditions.append({
            "type": "LicenseConflict",
            "status": "True",
            "reason": "DuplicateSecret",
            "message": (
                f"License secret is already claimed by ControlPlane "
                f"'{license_conflict}'. Each ControlPlane must use a unique "
                "license secret."
            )
        })

    if conditions:
        status["controlplane"]["conditions"] = conditions

    rsp.desired.composite.resource.update({"status": status})
