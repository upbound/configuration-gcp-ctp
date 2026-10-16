"""04b-k8gb - k8gb operator + CoreDNS producer (see aws-ctp docs/gslb-dns-architecture.md).

Installs the k8gb operator and its CoreDNS on the child GKE cluster, exposing
CoreDNS via two GCP external L4 LoadBalancers on one reserved regional static IP
(the chart's UDP Service and a composed TCP Service). The XR surfaces the k8gb
status contract (nsName, glueAddresses, delegationRecord, coreDNSEndpoint) for
the FleetGslb aggregator: it observes the chart Service for coreDNSEndpoint;
the glue comes from the reserved Address.

- Chart pinned to v0.20.0, which ships both `k8gb.absa.oss/v1beta1` (via
  `installLegacyCrds: true`, the default) and the new `k8gb.io/v1beta1` `Gslb`
  CRD, so configuration-resilient-ctp's consumer contract still holds - do not
  blindly track latest.
- `extdns.enabled: false`: this package is a producer only; the parent-side
  FleetGslb writes the NS delegation, not per-child external-dns.
- The Helm release name is pinned to `k8gb` (external-name) so its CoreDNS
  Service is `k8gb-coredns` in namespace `k8gb`, the name k8gb expects.
- k8gb needs CoreDNS on UDP and TCP 53 (aws-ctp docs/gslb-dns-architecture.md
  section 2). The chart-default mixed TCP+UDP:53 Service stayed pending
  (SyncLoadBalancerFailed) on a GKE 1.34 cluster. GKE documents one
  mixed-protocol Service from 1.34.1-gke.2190000 (GA 1.36.2-gke.1498000) with
  the HttpLoadBalancing add-on and a backend-service-based LB (before 1.37 a
  new Service must set
  `loadBalancerClass: networking.gke.io/l4-regional-external`), and the XRD
  still offers 1.32 and 1.33. So the chart Service is UDP-only
  (`coredns.servers[].zones[].use_tcp: false`) and TCP:53 comes from a second,
  TCP-only Service (`k8gb-coredns-tcp`) on the same reserved IP; GKE lets LB
  Services share a static IP when protocol/port differ. Both carry
  `cloud.google.com/l4-rbs: enabled` (backend-service RBS passthrough NLB),
  which is compatible with a pinned `loadBalancerIP`. No in-cluster LB
  controller is needed; GKE provisions the LBs natively.
"""

from crossplane.function import resource

from .prelude import stamp


def add_k8gb_resources(rsp, id_val, k8gb_param, geo_tag, ext_geo_tags,
                       k8gb_deployed, location, provider_config, address,
                       config):
    dns_zone = k8gb_param.get("dnsZone", "")
    parent_zone = k8gb_param.get("parentZone", "")

    # One reserved regional external IP, pinned on both CoreDNS LoadBalancers so
    # its glue is a stable IPv4 A record. The two LBs share one IP, so exactly
    # one Address (no per-subnet loop). This is a GCP MR (management creds via
    # provider_config), unlike the child Helm Release.
    address_mr = {
        "apiVersion": "compute.gcp.m.upbound.io/v1beta1",
        "kind": "Address",
        "metadata": {
            "name": f"{id_val}-k8gb-address",
            "namespace": config["namespace"],
            "annotations": {
                "crossplane.io/composition-resource-name": "k8gb-address"
            }
        },
        "spec": {
            "forProvider": {
                "addressType": "EXTERNAL",
                "region": location,
                "networkTier": "PREMIUM"
            },
            "providerConfigRef": {
                "name": provider_config,
                "kind": "ProviderConfig"
            }
        }
    }
    stamp(address_mr, config)
    resource.update(rsp.desired.resources["k8gb-address"], address_mr)

    # Hold the Release and the TCP Service (which create the LBs) until the
    # Address is reserved, so each LB is created once already bound to its
    # static IP - changing loadBalancerIP on a live GKE LB forces a re-provision.
    address_ready = bool(address)

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
        # Producer only - the parent (FleetGslb) writes the NS delegation.
        "extdns": {"enabled": False},
        "coredns": {
            "serviceType": "LoadBalancer",
            # UDP-only chart Service (TCP:53 is the composed k8gb-coredns-tcp
            # below). Reproduces the chart default servers block with use_tcp
            # flipped to false; CoreDNS itself still listens on TCP 5353.
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

    if address_ready:
        values["coredns"]["service"]["loadBalancerIP"] = address

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
    if address_ready:
        stamp(release, config)
        resource.update(rsp.desired.resources["k8gb-release"], release)

        # TCP-only Service on the same reserved IP as the chart's UDP Service.
        # Ready once GKE binds the TCP LB to the reserved IP.
        tcp_service = {
            "apiVersion": "kubernetes.m.crossplane.io/v1alpha1",
            "kind": "Object",
            "metadata": {
                "name": f"{id_val}-k8gb-coredns-tcp",
                "namespace": config["namespace"],
                "annotations": {
                    "crossplane.io/composition-resource-name": "k8gb-coredns-tcp"
                }
            },
            "spec": {
                "forProvider": {
                    "manifest": {
                        "apiVersion": "v1",
                        "kind": "Service",
                        # No app.kubernetes.io/name=coredns label: k8gb v0.20.0
                        # self-IP discovery takes the first Service with it.
                        "metadata": {
                            "name": "k8gb-coredns-tcp",
                            "namespace": "k8gb",
                            "annotations": {
                                # Kept to match the chart Service's LB type.
                                "cloud.google.com/l4-rbs": "enabled"
                            }
                        },
                        "spec": {
                            "type": "LoadBalancer",
                            "loadBalancerIP": address,
                            # Mirrors the chart's k8gb-coredns Service selector;
                            # re-check on a chart bump.
                            "selector": {
                                "app.kubernetes.io/instance": "k8gb",
                                "app.kubernetes.io/name": "coredns"
                            },
                            "ports": [
                                {
                                    "name": "tcp-53",
                                    "protocol": "TCP",
                                    "port": 53,
                                    "targetPort": 5353
                                }
                            ]
                        }
                    }
                },
                "readiness": {
                    "policy": "DeriveFromCelQuery",
                    "celQuery": f'has(object.status.loadBalancer.ingress) && object.status.loadBalancer.ingress.exists(i, has(i.ip) && i.ip == "{address}")'
                },
                "providerConfigRef": {
                    "name": id_val,
                    "kind": "ProviderConfig"
                }
            }
        }
        stamp(tcp_service, config)
        resource.update(rsp.desired.resources["k8gb-coredns-tcp"], tcp_service)

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
