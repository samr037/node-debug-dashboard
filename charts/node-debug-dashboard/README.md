# node-debug-dashboard

Helm chart for [node-debug-dashboard](https://github.com/samr037/node-debug-dashboard): hardware, storage, network, Kubernetes, and etcd diagnostics for Talos Linux nodes (works on any Kubernetes).

## Install

OCI registry (Helm 3.8+):

```bash
helm install ndd oci://ghcr.io/samr037/charts/node-debug-dashboard \
  --version 0.1.0 \
  --namespace node-debug --create-namespace
```

Or via `helm repo`:

```bash
helm repo add node-debug-dashboard https://samr037.github.io/node-debug-dashboard
helm repo update
helm install ndd node-debug-dashboard/node-debug-dashboard \
  --namespace node-debug --create-namespace
```

The dashboard is then on host port 80 of every node.

## Enabling SSH

SSH is off by default. To turn it on with a key:

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
| `ssh.enabled` | bool | `false` | Run sshd in the pod |
| `ssh.port` | int | `2022` | SSH listen port |
| `ssh.passwordAuth` | bool | `false` | Allow password auth |
| `ssh.authorizedKeys` | string | `""` | Newline-separated public keys |
| `hostNetwork` | bool | `true` | Required for full diagnostics |
| `hostPID` | bool | `true` | Required for processes/containers |
| `hostIPC` | bool | `true` | Required for some diagnostics |
| `privileged` | bool | `true` | Required for `/host` mount |
| `hostRootMount` | bool | `true` | Mount host `/` at `/host` |
| `serviceAccount.create` | bool | `true` | Create a ServiceAccount |
| `rbac.create` | bool | `true` | Create ClusterRole + Binding |
| `tolerations` | list | `[{operator: Exists}]` | Run on every node |
| `resources` | map | see `values.yaml` | Pod resource requests/limits |

See [`values.yaml`](./values.yaml) for the full list.

## Security

The chart deploys a privileged DaemonSet with the host root mounted at `/host`. The image contains hardcoded `debug:debug` and `root:root` passwords; they are only reachable when both `ssh.enabled=true` and `ssh.passwordAuth=true`. Use this on private clusters only, and prefer key-based SSH.

## License

MIT — see [LICENSE](https://github.com/samr037/node-debug-dashboard/blob/main/LICENSE).
