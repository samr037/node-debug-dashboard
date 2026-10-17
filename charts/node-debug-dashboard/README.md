# node-debug-dashboard

Helm chart for [node-debug-dashboard](https://github.com/samr037/node-debug-dashboard): hardware, storage, network, Kubernetes, and etcd diagnostics for Talos Linux nodes (works on any Kubernetes).

## Install

The DaemonSet is privileged + hostPath + hostNetwork, so the target
namespace needs the PodSecurity `privileged` label. Create the
namespace and label it first:

```bash
kubectl create ns node-debug
kubectl label ns node-debug pod-security.kubernetes.io/enforce=privileged --overwrite
```

Then install via OCI (Helm 3.8+):

```bash
helm install ndd oci://ghcr.io/samr037/charts/node-debug-dashboard \
  --version 0.4.0 \
  -n node-debug
```

Or via `helm repo`:

```bash
helm repo add node-debug-dashboard https://samr037.github.io/node-debug-dashboard
helm repo update
helm install ndd node-debug-dashboard/node-debug-dashboard -n node-debug
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
| `auth.disabled` | bool | `false` | Serve without auth instead of generating a password when no credential is set |
| `auth.username` | string | `debug` | HTTP Basic username |
| `auth.token` | string | `""` | Bearer token, for scripts only (stored in a chart-managed Secret) |
| `auth.password` | string | `""` | HTTP Basic password, what the browser UI prompts for (stored in a chart-managed Secret) |
| `auth.existingSecret` | string | `""` | Read the credentials from this Secret instead (preferred) |
| `auth.tokenKey` / `auth.passwordKey` | string | `token` / `password` | Keys in `auth.existingSecret` |
| `dmesgWindowHours` | string | `""` (one week) | Kernel log window scanned for faults |
| `counterWindowHours` | string | `""` (24) | NIC CRC/carrier and ECC counters are warning/critical only while they increased within this window, `info` after |
| `networkPolicy.enabled` | bool | `false` | Render a NetworkPolicy (no effect with `hostNetwork=true`) |
| `networkPolicy.allowedCIDRs` | list | `[]` | Source CIDRs allowed in |
| `networkPolicy.allowedNamespaces` | list | `[]` | Source namespaces allowed in |
| `ssh.enabled` | bool | `false` | Run sshd in the pod |
| `ssh.port` | int | `2022` | SSH listen port |
| `ssh.passwordAuth` | bool | `false` | Allow password auth (needs `ssh.password` or `ssh.existingSecret`) |
| `ssh.authorizedKeys` | string | `""` | Newline-separated public keys |
| `ssh.password` | string | `""` | Password for the `debug` user (stored in a chart-managed Secret) |
| `ssh.existingSecret` / `ssh.passwordKey` | string | `""` / `password` | Read the SSH password from a Secret instead |
| `ssh.persistHostKeys` | bool | `true` | Keep SSH host keys in a per-node hostPath, so they survive pod restarts |
| `ssh.hostKeyPath` | string | `/var/lib/node-debug-dashboard/ssh` | That hostPath (also passed as `SSH_HOST_KEY_DIR`) |
| `hostNetwork` | bool | `true` | Required for full diagnostics; when `false`, ports are published as hostPorts |
| `dnsPolicy` | string | `""` (follows `hostNetwork`) | Override the pod DNS policy |
| `hostPID` | bool | `true` | Required for processes/containers |
| `hostIPC` | bool | `true` | Required for some diagnostics |
| `privileged` | bool | `true` | Required for `/host` mount |
| `hostRootMount` | bool | `true` | Mount host `/` at `/host` |
| `hostDevMount` / `hostSysMount` | bool | `true` | Mount host `/dev` and `/sys` explicitly |
| `serviceAccount.create` | bool | `true` | Create a ServiceAccount |
| `serviceAccount.name` | string | `""` | Required when `create=false` and `rbac.create=true` |
| `rbac.create` | bool | `true` | Create ClusterRole + Binding (named `<namespace>-<fullname>`) |
| `livenessProbe` / `readinessProbe` | map | `httpGet /api/health` | Container probes; `{}` drops one |
| `tolerations` | list | `[{operator: Exists}]` | Run on every node |
| `resources` | map | see `values.yaml` | Pod resource requests/limits |

See [`values.yaml`](./values.yaml) for the full list.

## Security

The chart deploys a privileged DaemonSet with the host root mounted at `/host`. Use it on private clusters only.

- **Dashboard auth:** with no `auth.token`, `auth.password` or `auth.existingSecret`, `helm install`/`upgrade` generate a random HTTP Basic password (user `debug`), keep it across upgrades, and NOTES.txt prints the `kubectl` command to read it. `helm template` — and so Argo CD and Flux — has no cluster to keep a generated value in, so it renders with auth **off**; set `auth.existingSecret` there. `auth.disabled=true` opts out. The browser UI needs a password (Basic); a token alone is for scripts. Best is a Secret you manage, holding `token` and/or `password` keys:

  ```bash
  kubectl -n node-debug create secret generic ndd-auth --from-literal=password='...'
  helm upgrade --install ndd node-debug-dashboard/node-debug-dashboard -n node-debug \
    --set auth.existingSecret=ndd-auth --set auth.password=on
  ```

  (`auth.password=on` only selects which key to wire; with neither switch both keys are wired and must exist.) `auth.token` / `auth.password` given as plain values go into a chart-managed Secret, never the pod spec. `/api/health` stays open for the kubelet probes.
- **SSH:** the image ships `debug` and `root` locked; there is no default password. Password auth only works for `debug` with `ssh.password` or `ssh.existingSecret`; otherwise the entrypoint keeps it off. Prefer `ssh.authorizedKeys`.
- **NetworkPolicy** (`networkPolicy.enabled`) needs `allowedCIDRs` and/or `allowedNamespaces`, and does nothing while `hostNetwork=true`: CNIs do not enforce policy on host-network pods. Restrict access with a host firewall (Talos `NetworkRuleConfig`, Cilium host policies) or an upstream ACL.
- **RBAC** is read-only on nodes, pods, services, endpoints, events and `metrics.k8s.io`. It deliberately excludes `nodes/proxy`, which would allow exec into any pod.

## License

MIT — see [LICENSE](https://github.com/samr037/node-debug-dashboard/blob/main/LICENSE).
