"""09b-gateway - Envoy Gateway (Kubernetes Gateway API) data plane.

Replaces the retired community ingress-nginx (kubernetes/ingress-nginx archived
2026-03-24). Installed when an add-on that needs an HTTP data plane is enabled
(k8gb or argocd). The Envoy Gateway controller provisions NO cloud LB until a
Gateway resource is created, so a k8gb-only plane pays nothing here until an app
appears.

- Chart is OCI-only: oci://docker.io/envoyproxy/gateway-helm. CRDs (Gateway API +
  EnvoyProxy) are bundled by default (crds.enabled=true) - one Release installs
  everything.
- The GatewayClass `eg` points at an EnvoyProxy CR whose data-plane Service is a
  plain `type: LoadBalancer`; GKE provisions an external passthrough Network LB
  natively (no LB-controller add-on is needed, unlike the AWS sibling). The
  HTTP(S) plane is single-protocol TCP, so it needs neither the k8gb CoreDNS
  mixed-protocol RBS annotation nor an AWS-style NLB annotation block.
- The EnvoyProxy + GatewayClass Objects wait on the release being deployed so the
  CRDs exist first (same pattern as the argocd app / knative CR gates).
"""

from crossplane.function import resource

from .prelude import stamp


def _child_object(id_val, cr_name, manifest, config):
    return {
        "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
        "kind": "Object",
        "metadata": {
            "name": f"{id_val}-{cr_name}",
            "namespace": config["namespace"],
            "annotations": {
                "crossplane.io/composition-resource-name": cr_name
            }
        },
        "spec": {
            "forProvider": {"manifest": manifest},
            "providerConfigRef": {"name": id_val, "kind": "ProviderConfig"}
        }
    }


def add_gateway_resources(rsp, id_val, gateway_ready, config):
    annotations = {
        "crossplane.io/composition-resource-name": "envoy-gateway-release"
    }
    # provider-helm stale-Ready workaround (same as uxp.py).
    if gateway_ready:
        annotations["crossplane.io/ready"] = "True"

    release = {
        "apiVersion": "helm.m.crossplane.io/v1beta1",
        "kind": "Release",
        "metadata": {
            "name": f"{id_val}-envoy-gateway",
            "namespace": config["namespace"],
            "annotations": annotations
        },
        "spec": {
            "forProvider": {
                "chart": {
                    "name": "gateway-helm",
                    # OCI registry - no HTTPS repo exists for Envoy Gateway.
                    "repository": "oci://docker.io/envoyproxy",
                    # renovate: datasource=docker depName=envoyproxy/gateway-helm
                    "version": "v1.8.2"
                },
                "namespace": "envoy-gateway-system",
                "skipCreateNamespace": False,
                "wait": True
            },
            "providerConfigRef": {
                "name": id_val,
                "kind": "ProviderConfig"
            }
        }
    }
    stamp(release, config)
    resource.update(rsp.desired.resources["envoy-gateway-release"], release)

    # EnvoyProxy + GatewayClass need the Envoy Gateway CRDs, so wait until the
    # release is deployed.
    if not gateway_ready:
        return

    envoy_proxy = _child_object(id_val, "envoy-proxy-config", {
        "apiVersion": "gateway.envoyproxy.io/v1alpha1",
        "kind": "EnvoyProxy",
        "metadata": {"name": "eg-proxy", "namespace": "envoy-gateway-system"},
        "spec": {
            "provider": {
                "type": "Kubernetes",
                "kubernetes": {
                    # Plain LoadBalancer -> GKE external passthrough Network LB.
                    # No annotations: the plane is single-protocol TCP (80/443),
                    # so it needs neither the CoreDNS mixed-protocol RBS
                    # annotation nor an AWS-style NLB annotation block.
                    "envoyService": {"type": "LoadBalancer"}
                }
            }
        }
    }, config)
    stamp(envoy_proxy, config)
    resource.update(rsp.desired.resources["envoy-proxy-config"], envoy_proxy)

    gateway_class = _child_object(id_val, "gateway-class", {
        "apiVersion": "gateway.networking.k8s.io/v1",
        "kind": "GatewayClass",
        "metadata": {"name": "eg"},
        "spec": {
            "controllerName": "gateway.envoyproxy.io/gatewayclass-controller",
            "parametersRef": {
                "group": "gateway.envoyproxy.io",
                "kind": "EnvoyProxy",
                "name": "eg-proxy",
                "namespace": "envoy-gateway-system"
            }
        }
    }, config)
    stamp(gateway_class, config)
    resource.update(rsp.desired.resources["gateway-class"], gateway_class)
