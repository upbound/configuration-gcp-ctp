"""08-vpa — Vertical Pod Autoscaler Helm Release.

VPA needs the Kubernetes metrics API to compute pod recommendations. GKE
ships metrics-server as a managed addon (in kube-system, owned by the
cluster), so this GCP variant only installs VPA itself. Trying to install
Fairwinds' bundled metrics-server alongside the GKE addon fails because
provider-helm refuses to adopt resources without Helm ownership annotations.

The AWS sibling installs metrics-server explicitly because EKS does not
ship one by default. Ported from configuration-azure-ctp/vpa.py.

Note: GKE also ships its own VerticalPodAutoscaler addon. This package
installs the Fairwinds VPA chart (the same one UXP's provider-VPA capability
expects) rather than enabling the GKE addon, to keep the CRD/version surface
identical to the Azure and AWS siblings.
"""

from crossplane.function import resource

from .prelude import stamp


def add_vpa_resources(rsp, id_val, vpa, vpa_ready, config):
    vpa_release = {
        "apiVersion": "helm.m.crossplane.io/v1beta1",
        "kind": "Release",
        "metadata": {
            "name": f"{id_val}-vpa",
            "namespace": "default",
            "annotations": {
                "crossplane.io/composition-resource-name": "vpa-release"
            }
        },
        "spec": {
            "forProvider": {
                "chart": {
                    "name": "vpa",
                    "repository": "https://charts.fairwinds.com/stable",
                    # renovate: datasource=helm depName=vpa registryUrl=https://charts.fairwinds.com/stable
                    "version": "4.10.1"
                },
                "namespace": "kube-system",
                "skipCreateNamespace": False,
                "wait": True
            },
            "providerConfigRef": {
                "name": id_val,
                "kind": "ProviderConfig"
            }
        }
    }
    stamp(vpa_release, config)
    resource.update(rsp.desired.resources["vpa-release"], vpa_release)
