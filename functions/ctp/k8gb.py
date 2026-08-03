"""04b-k8gb - k8gb operator + CoreDNS producer (see aws-ctp docs/gslb-dns-architecture.md).

Installs the k8gb operator and its CoreDNS on the child GKE cluster, exposing
CoreDNS via a single UDP-only GCP external L4 LoadBalancer pinned to one reserved
regional static IP, and observes that Service so the XR can surface the k8gb
status contract (nsName, glueAddresses, delegationRecord, coreDNSEndpoint) for
the FleetGslb aggregator to consume.

- Chart pinned to v0.20.0, which ships both `k8gb.absa.oss/v1beta1` (via
  `installLegacyCrds: true`, the default) and the new `k8gb.io/v1beta1` `Gslb`
  CRD, so configuration-resilient-ctp's consumer contract still holds - do not
  blindly track latest.
- `extdns.enabled: false`: this package is a producer only; the parent-side
  FleetGslb writes the NS delegation, not per-child external-dns.
- The Helm release name is pinned to `k8gb` (external-name) so its CoreDNS
  Service is `k8gb-coredns` in namespace `k8gb`, the name k8gb expects.
- CoreDNS is forced to a single UDP-only Service (`coredns.servers[].zones[].use_tcp:
  false`). A mixed TCP+UDP:53 Service is rejected by GKE's L4 LB before
  v1.36.2-gke.1498000 (SyncLoadBalancerFailed), so the chart-default mixed Service
  never provisions on GKE <= 1.34; UDP-only provisions on every GKE version and is
  k8gb's canonical shape (no AXFR, small responses, so no TCP needed). The
  `cloud.google.com/l4-rbs: enabled` annotation (backend-service RBS passthrough
  NLB) is kept - it is compatible with a UDP-only Service and with a pinned
  `loadBalancerIP`. No LB-controller add-on is needed; GKE provisions the LB
  natively.
"""

from crossplane.function import resource

from .prelude import stamp


def add_k8gb_resources(rsp, id_val, k8gb_param, geo_tag, ext_geo_tags,
                       k8gb_deployed, config):
    dns_zone = k8gb_param.get("dnsZone", "")
    parent_zone = k8gb_param.get("parentZone", "")

    values = {
        "k8gb": {
            "deployCrds": True,
            "deployRbac": True,
            "clusterGeoTag": geo_tag,
            "extGslbClustersGeoTags": ext_geo_tags,
            "dnsZones": [
                {
                    "loadBalancedZone": dns_zone,
                    "parentZone": parent_zone
                }
            ],
            "edgeDNSServers": ["1.1.1.1"]
        },
        # Producer only — the parent (FleetGslb) writes the NS delegation.
        "extdns": {"enabled": False},
        "coredns": {
            "serviceType": "LoadBalancer",
            # UDP-only single Service. A mixed TCP+UDP:53 Service is rejected by
            # GKE's L4 LB before v1.36.2 (SyncLoadBalancerFailed); UDP-only
            # provisions on every GKE version and is k8gb's canonical shape (it
            # needs no TCP - no AXFR, small responses). Reproduces the chart
            # default servers block with use_tcp flipped to false.
            "servers": [
                {
                    "zones": [{"zone": ".", "use_tcp": False}],
                    "port": 5353,
                    "servicePort": 53,
                    "plugins": [
                        {"name": "prometheus", "parameters": "0.0.0.0:9153"}
                    ]
                }
            ],
            "service": {
                "annotations": {
                    # Backend-service (RBS) external passthrough NLB; compatible
                    # with a UDP-only Service and with loadBalancerIP.
                    "cloud.google.com/l4-rbs": "enabled"
                }
            }
        }
    }

    release_annotations = {
        "crossplane.io/composition-resource-name": "k8gb-release",
        # Pin the Helm release name so CoreDNS is `k8gb-coredns` in ns `k8gb`.
        "crossplane.io/external-name": "k8gb"
    }
    if k8gb_deployed:
        release_annotations["crossplane.io/ready"] = "True"

    release = {
        "apiVersion": "helm.m.crossplane.io/v1beta1",
        "kind": "Release",
        "metadata": {
            "name": f"{id_val}-k8gb",
            "namespace": config["namespace"],
            "annotations": release_annotations
        },
        "spec": {
            "forProvider": {
                "chart": {
                    "name": "k8gb",
                    "repository": "https://www.k8gb.io",
                    # renovate: datasource=helm depName=k8gb registryUrl=https://www.k8gb.io
                    # v0.20.0 still ships gslbs.k8gb.absa.oss (installLegacyCrds
                    # default) - the producer/consumer contract with resilient-ctp holds.
                    "version": "v0.20.0"
                },
                "namespace": "k8gb",
                "skipCreateNamespace": False,
                "wait": True,
                "values": values
            },
            "providerConfigRef": {
                "name": id_val,
                "kind": "ProviderConfig"
            }
        }
    }
    stamp(release, config)
    resource.update(rsp.desired.resources["k8gb-release"], release)

    # Observe-only Object on the child CoreDNS Service to read its LB endpoint.
    coredns_observe = {
        "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
        "kind": "Object",
        "metadata": {
            "name": f"{id_val}-k8gb-coredns",
            "namespace": config["namespace"],
            "annotations": {
                "crossplane.io/composition-resource-name": "k8gb-coredns-observe"
            }
        },
        "spec": {
            "managementPolicies": ["Observe"],
            "forProvider": {
                "manifest": {
                    "apiVersion": "v1",
                    "kind": "Service",
                    "metadata": {
                        "name": "k8gb-coredns",
                        "namespace": "k8gb"
                    }
                }
            },
            "providerConfigRef": {
                "name": id_val,
                "kind": "ProviderConfig"
            }
        }
    }
    stamp(coredns_observe, config)
    resource.update(rsp.desired.resources["k8gb-coredns-observe"], coredns_observe)
