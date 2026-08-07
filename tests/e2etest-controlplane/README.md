# ControlPlane E2E test

Provisions one real GKE ControlPlane and asserts Ready=True (UXP + Workload
Identity GCS backup + k8gb producer + ArgoCD + cert-manager + Envoy Gateway).
Uses Upbound-injected identity; installation-only. Run with
`up test run tests/e2etest-controlplane --e2e`.
