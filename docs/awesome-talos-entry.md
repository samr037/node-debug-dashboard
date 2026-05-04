# Draft entry for siderolabs/awesome-talos

Proposed PR text for adding `node-debug-dashboard` to
[awesome-talos](https://github.com/siderolabs/awesome-talos), once
v0.1.0 is published and the container image and Helm chart are
pullable.

## Suggested section

Tools, or a Diagnostics / Debugging subsection if one exists.

## Suggested line

```markdown
- [node-debug-dashboard](https://github.com/samr037/node-debug-dashboard) - Privileged DaemonSet exposing hardware, storage (SMART), network, Kubernetes, and etcd diagnostics for Talos nodes. Web UI, REST API, and opt-in SSH shell with `ndiag-*` / `kdiag-*` scripts. Helm chart, multi-arch image.
```

## PR description

```
Adds node-debug-dashboard.

What it does:
- Runs as a privileged DaemonSet on every node.
- Exposes a web dashboard, REST API (Swagger at /docs), and opt-in
  SSH debug shell.
- Diagnostics: CPU/RAM/ECC, PCI/USB, NICs, sensors, NVIDIA GPUs,
  SMART (ATA + NVMe), UEFI boot order, K8s node conditions and PKI,
  etcd metrics, Talos machine type / extensions / schematic,
  container memory stats, live container log streaming.
- Curated `ndiag-*` and `kdiag-*` scripts in the SSH shell.

Talos-specific bits:
- Reads etcd certs from /system/secrets/etcd/.
- Recognises Talos machine type and extensions.
- Reports schematic ID and EFI boot entries.

Distribution:
- Image:  ghcr.io/samr037/node-debug-dashboard (linux/amd64, linux/arm64)
- Chart:  oci://ghcr.io/samr037/charts/node-debug-dashboard
- License: MIT
```
