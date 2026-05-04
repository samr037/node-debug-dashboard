# node-debug-dashboard

Hardware monitoring, Kubernetes diagnostics, and SSH debug shell for Talos Linux (and any K8s) nodes.

See the [main project README](https://github.com/samr037/node-debug-dashboard) for screenshots and feature details.

## TL;DR

```bash
helm install ndd oci://ghcr.io/samr037/charts/node-debug-dashboard \
  --namespace node-debug --create-namespace
```

Then open `http://<any-node-ip>/` for the dashboard.

## Install

### From OCI registry (Helm 3.8+)

```bash
helm install ndd oci://ghcr.io/samr037/charts/node-debug-dashboard \
  --version 0.1.0 \
  --namespace node-debug --create-namespace
```

### From `helm repo`

```bash
helm repo add node-debug-dashboard https://samr037.github.io/node-debug-dashboard
helm repo update
helm install ndd node-debug-dashboard/node-debug-dashboard \
  --namespace node-debug --create-namespace
```

## Enabling SSH

SSH is **disabled by default**. To enable with key-based auth:

```bash
helm upgrade --install ndd node-debug-dashboard/node-debug-dashboard \
  --namespace node-debug --create-namespace \
  --set ssh.enabled=true \
  --set-file ssh.authorizedKeys=$HOME/.ssh/id_ed25519.pub
```

Then `ssh debug@<node-ip> -p 2022`.

## Values

| Key | Type | Default | Description |
|---|---|---|---|
| `image.repository` | string | `ghcr.io/samr037/node-debug-dashboard` | Image repository |
| `image.tag` | string | `""` (uses `.Chart.AppVersion`) | Image tag |
| `image.pullPolicy` | string | `IfNotPresent` | Image pull policy |
| `httpPort` | int | `80` | Host port for the dashboard / API |
| `ssh.enabled` | bool | `false` | Enable the SSH debug shell |
| `ssh.port` | int | `2022` | SSH listen port |
| `ssh.passwordAuth` | bool | `false` | Allow password auth (insecure — use keys) |
| `ssh.authorizedKeys` | string | `""` | Newline-separated public keys |
| `hostNetwork` | bool | `true` | Required for full diagnostics |
| `hostPID` | bool | `true` | Required for processes/containers |
| `hostIPC` | bool | `true` | Required for some diagnostics |
| `privileged` | bool | `true` | Required for `/host` mount |
| `hostRootMount` | bool | `true` | Mount host `/` at `/host` |
| `serviceAccount.create` | bool | `true` | Create dedicated ServiceAccount |
| `rbac.create` | bool | `true` | Create ClusterRole + Binding |
| `tolerations` | list | `[{operator: Exists}]` | Run on every node |
| `resources` | map | see `values.yaml` | Pod resource requests/limits |

See [`values.yaml`](./values.yaml) for the full list.

## Security

This chart deploys a **privileged** DaemonSet with full host access. It is intended for internal cluster debugging, not for clusters exposed to untrusted users. The container ships with default passwords (`debug:debug`, `root:root`) that are only reachable when `ssh.enabled=true` *and* `ssh.passwordAuth=true` — both off by default.

## License

MIT — see [LICENSE](https://github.com/samr037/node-debug-dashboard/blob/main/LICENSE).
