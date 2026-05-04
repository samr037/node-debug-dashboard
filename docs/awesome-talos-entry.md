# Draft entry for siderolabs/awesome-talos

This is the proposed PR text for adding `node-debug-dashboard` to the
[awesome-talos](https://github.com/siderolabs/awesome-talos) list, once a
public release is cut and the image + Helm chart are published.

## Suggested section

Likely fits under **Tools** or a dedicated **Diagnostics / Debugging** subsection.

## Suggested line

```markdown
- [node-debug-dashboard](https://github.com/samr037/node-debug-dashboard) - Privileged DaemonSet that surfaces hardware, storage (SMART), Kubernetes, and etcd diagnostics for Talos nodes via web UI, REST API, and an opt-in SSH debug shell. Pure observability — no host mutation. Helm chart and multi-arch image available.
```

## PR description

```
This PR adds node-debug-dashboard to the list.

What it does:
- Runs as a privileged DaemonSet on every Talos node.
- Exposes a web dashboard, REST API (Swagger at /docs), and opt-in SSH
  debug shell.
- Diagnostics include: CPU/RAM/ECC, PCI/USB, NICs, sensors, NVIDIA GPUs,
  SMART (ATA + NVMe), UEFI boot order, K8s node conditions/PKI, etcd
  deep metrics, Talos machine type/extensions/schematic, container
  memory stats, and live container log streaming.
- Curated `ndiag-*` and `kdiag-*` scripts inside the SSH shell.
- Talos-aware: reads etcd certs from `/system/secrets/etcd/`, recognises
  Talos machine type and extensions, surfaces schematic ID.

Why it's relevant:
Talos's immutable, shell-less posture is one of its strengths, but it
makes node-level debugging harder than on a traditional distro. This
tool fills that gap without modifying the host.

Distribution:
- Multi-arch container image: ghcr.io/samr037/node-debug-dashboard
- Helm chart: oci://ghcr.io/samr037/charts/node-debug-dashboard
- License: MIT
```
