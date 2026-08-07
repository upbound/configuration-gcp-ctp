# GCP GKE Control Plane composition function

The Python composition function for `configuration-gcp-ctp`. Entrypoint:
`function.main:cli`; composition logic in `function/fn.py` (`compose`), with one
sibling module per composed section (network, gke, uxp, backup, k8gb, argo, ...).
