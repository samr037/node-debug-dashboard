# SSH Debug Shell

The Node Debug Dashboard includes an SSH server (port 2022) with **zsh + oh-my-zsh**, 200+ pre-installed diagnostic tools, and custom diagnostic scripts — turning every Kubernetes node into a full debugging workstation.

![MOTD](screenshots/ssh-motd.png)

## Connecting

```bash
# Password auth (default: debug/debug)
ssh -p 2022 debug@<node-ip>

# Root access
ssh -p 2022 root@<node-ip>

# With key auth
ssh -p 2022 -i ~/.ssh/id_ed25519 debug@<node-ip>
```

Both users have full `sudo` access. The `debug` user is recommended for daily use.

## Shell Environment

| Feature | Details |
|---|---|
| Shell | Zsh with oh-my-zsh (agnoster theme) |
| Editor | Vim with custom config (syntax, line numbers, status line) |
| Prompt | Shows `user@node-name` (Kubernetes node name) |
| Plugins | git, docker, kubectl, colored-man-pages, autosuggestions, syntax-highlighting |
| History | 10,000 entries, shared across sessions, dedup |

## Quick Reference

Type `aliases` for the full list, or `help-ndiag` for diagnostic script docs.

### Host Access

| Alias | Description |
|---|---|
| `hostns` | Enter full host namespace (mount, net, pid) |
| `hostsh` | Host namespace with `/bin/sh` |
| `hostcmd <cmd>` | Run a single command in host namespace |
| `hroot` / `hlog` / `hpods` | Navigate to host filesystem locations |
| `hetc` / `hkube` / `hproc` | More host filesystem shortcuts |

### Containers (crictl)

| Alias | Description |
|---|---|
| `cps` / `cpsa` | List running / all containers |
| `cpods` | List pods |
| `clog <id>` / `clogf <id>` | Container logs / follow logs |
| `cinsp <id>` | Inspect container details |
| `cstats` | Container resource stats |
| `cexec <id>` | Exec into container |

### Kubernetes (via ServiceAccount)

| Alias | Description |
|---|---|
| `kn` | List cluster nodes |
| `kp` | List all pods (all namespaces) |
| `kpn` | List pods on current node only |
| `kevents` | Last 20 cluster events |

### Networking

| Alias | Description |
|---|---|
| `ports` / `portsu` | TCP / UDP listeners |
| `conns` | Active TCP connections |
| `ifaces` | Network interfaces (brief) |
| `routes` | Routing table |
| `listen` | LISTEN sockets only |
| `pubip` | External IP |
| `tcpd` / `sniff` | tcpdump shortcuts |

### Processes & System

| Alias | Description |
|---|---|
| `topmem` / `topcpu` | Top processes by memory / CPU |
| `psmem` / `pscpu` | ps sorted by memory / CPU |
| `loadavg` | Current load average |
| `memfree` | `free -h` |
| `iostats` | I/O statistics (5 samples) |

### Disk & Storage

| Alias | Description |
|---|---|
| `dfu` / `dfi` | Disk usage / inode usage |
| `lsblk` | Block devices with model info |
| `smart <dev>` | SMART data for a device |
| `smartall` | Quick SMART health for all disks |

### Hardware

| Alias | Description |
|---|---|
| `cpuinfo` | `lscpu` |
| `meminfo` | DIMM inventory |
| `pcidev` | PCI devices (verbose) |
| `gpuinfo` | `nvidia-smi` (via nsenter) |
| `gputop` | GPU utilization monitor |
| `sensors` | Temperature readings |

### Logs

| Alias | Description |
|---|---|
| `dmesg` | Kernel log (timestamped, colored) |
| `podlogs` | List pod log directories |
| `podlog <name>` | Tail logs for a pod (fuzzy match) |

## Diagnostic Scripts

Five hardware diagnostic scripts are included, each with subcommands and built-in help. Tab completion works for all subcommands.

![ndiag-cpu + ndiag-mem](screenshots/ssh-ndiag-hw.png)

### ndiag-cpu

CPU diagnostics — frequency, load, top consumers, thermal throttling.

```bash
ndiag-cpu              # Full report (same as 'all')
ndiag-cpu top          # Top 15 CPU consumers
ndiag-cpu freq         # Per-core frequency + governor
ndiag-cpu load         # Load average + CPU time breakdown
ndiag-cpu throttle     # Thermal zones + dmesg throttle events
ndiag-cpu --help       # Full documentation
```

### ndiag-mem

Memory diagnostics — usage, DIMM inventory, swap, OOM detection.

```bash
ndiag-mem              # Full report
ndiag-mem usage        # Memory gauge + breakdown
ndiag-mem top          # Top 15 RSS consumers
ndiag-mem dimms        # Physical DIMM layout + ECC status
ndiag-mem swap         # Swap usage + top swap consumers
ndiag-mem oom          # OOM kills + memory pressure (PSI)
ndiag-mem --help       # Full documentation
```

### ndiag-net

Network diagnostics — interfaces, connections, DNS, connectivity.

```bash
ndiag-net              # Full report
ndiag-net ifaces       # Interfaces + errors + speed
ndiag-net conns        # Active TCP connections
ndiag-net listen       # All listening ports
ndiag-net dns          # DNS resolution tests
ndiag-net reach        # Internet, K8s API, DNS, gateway checks
ndiag-net capture      # Quick 50-packet tcpdump
ndiag-net --help       # Full documentation
```

### ndiag-disk

Disk diagnostics — SMART health, I/O stats, usage, benchmarks.

```bash
ndiag-disk             # Full report (health + usage + io)
ndiag-disk health      # SMART health + key attributes
ndiag-disk io          # Live I/O stats (1s sample)
ndiag-disk usage       # Disk space + inode usage
ndiag-disk bench       # Quick 256MB sequential R/W test
ndiag-disk --help      # Full documentation
```

### ndiag-part

Partition diagnostics — mounts, LVM, filesystems, partition tables.

```bash
ndiag-part             # Full report
ndiag-part mounts      # Active mounts (filtered)
ndiag-part lvm         # LVM layout + software RAID
ndiag-part fs          # Filesystem tree + UUIDs
ndiag-part table       # GPT/MBR partition tables
ndiag-part --help      # Full documentation
```

## Kubernetes Diagnostic Scripts

Six `kdiag-*` scripts provide deep K8s visibility from inside the node. All use the pod's ServiceAccount token and (on control plane nodes) direct etcd client certs.

| kdiag-node | kdiag-etcd (CP) |
|---|---|
| ![kdiag-node](screenshots/ssh-kdiag-node.png) | ![kdiag-etcd](screenshots/ssh-kdiag-etcd.png) |

| kdiag-certs | kdiag-services |
|---|---|
| ![kdiag-certs](screenshots/ssh-kdiag-certs.png) | ![kdiag-services](screenshots/ssh-kdiag-services.png) |

### kdiag-node

Node health — conditions, resource allocation, taints, pressure, kubelet.

```bash
kdiag-node              # Full report
kdiag-node status       # Conditions, version, OS, runtime
kdiag-node resources    # Capacity vs allocatable vs pod requests (with bars)
kdiag-node taints       # Taints, labels, annotations
kdiag-node pressure     # PSI (CPU/memory/IO) + K8s pressure conditions
kdiag-node kubelet      # Component processes, static pods, containerd
```

### kdiag-pods

Pod diagnostics — sick detection, resource usage, images.

```bash
kdiag-pods              # Full report
kdiag-pods list         # All pods on this node
kdiag-pods sick         # CrashLoopBackOff, OOMKilled, Pending, high restarts
kdiag-pods resources    # Per-pod CPU/memory/GPU requests
kdiag-pods images       # Container image inventory with counts
kdiag-pods logs         # Pod log directories sorted by activity
```

### kdiag-etcd

etcd deep dive (control plane nodes only).

```bash
kdiag-etcd              # Full report
kdiag-etcd health       # Health check, version, process
kdiag-etcd members      # Member list + leader identification
kdiag-etcd size         # DB size, fragmentation, quota gauge
kdiag-etcd alarms       # Active alarms (NOSPACE, CORRUPT)
kdiag-etcd perf         # Write/read latency + WAL fsync benchmark
kdiag-etcd keys         # Key count by /registry/* prefix
```

### kdiag-certs

Certificate audit with color-coded expiry.

```bash
kdiag-certs             # Full audit
kdiag-certs k8s         # Kubernetes PKI certs (apiserver, kubelet, etc.)
kdiag-certs etcd        # etcd certs (ca, server, peer, healthcheck)
kdiag-certs sa          # ServiceAccount token decode + validity test
kdiag-certs tls         # Live TLS check on apiserver, etcd, kubelet
```

Color coding: green (>90d), yellow (30-90d), red (<30d).

### kdiag-services

Service & DNS debugging.

```bash
kdiag-services          # Full report
kdiag-services list     # All cluster services with type, IP, ports
kdiag-services dns      # CoreDNS health + resolution tests
kdiag-services endpoints  # Endpoint readiness per namespace
kdiag-services connectivity  # Component matrix: apiserver, DNS, etcd, kubelet, internet
```

### kdiag-events

Smart event viewer with grouping and live streaming.

```bash
kdiag-events            # Events for this node (default)
kdiag-events node       # Events involving this node
kdiag-events warnings   # Cluster-wide warnings grouped by reason
kdiag-events all        # All recent events (last 50)
kdiag-events ns gpu     # Events in the "gpu" namespace
kdiag-events watch      # Live event stream (Ctrl+C to stop)
```

## Pre-installed Tools

The container ships with 200+ tools organized by category:

| Category | Tools |
|---|---|
| **Network** | tcpdump, tshark, nmap, ncat, socat, iperf3, mtr, traceroute, ethtool, iptables, nftables, conntrack, curl, wget, dig |
| **Process** | htop, btop, strace, ltrace, lsof, sysstat (iostat, mpstat, pidstat, sar), iotop, dstat |
| **Disk** | smartmontools, hdparm, nvme-cli, fio, blktrace, fdisk, gdisk, parted, lvm2, mdadm |
| **Hardware** | dmidecode, lshw, lspci, lsusb, cpuid, numactl, hwinfo, efibootmgr, sensors |
| **Stress** | stress-ng, memtester |
| **Container** | crictl (container runtime interface) |
| **Editors** | vim (configured), nano |
| **Terminal** | tmux, screen, zsh, oh-my-zsh |
| **Utilities** | jq, tree, git, openssl, rsync, tar, gzip, xz, zstd |

## Configuration

| Environment Variable | Default | Description |
|---|---|---|
| `SSH_ENABLED` | `true` | Enable/disable SSH server |
| `SSH_PORT` | `2022` | SSH listen port |
| `SSH_PASSWORD_AUTH` | `true` | Allow password login |
| `SSH_AUTHORIZED_KEYS` | — | Newline-separated public keys |

### Key-based Auth via Kubernetes Secret

```yaml
env:
  - name: SSH_AUTHORIZED_KEYS
    valueFrom:
      secretKeyRef:
        name: ssh-keys
        key: authorized_keys
```

Or mount as a file:

```yaml
volumeMounts:
  - name: ssh-keys
    mountPath: /root/.ssh/authorized_keys_mount
    readOnly: true
volumes:
  - name: ssh-keys
    secret:
      secretName: ssh-authorized-keys
```

## Vim Keybindings

| Key | Action |
|---|---|
| `Space` | Leader key |
| `<leader>w` | Save |
| `<leader>q` | Quit |
| `<leader>fh` | Open `/host/` |
| `<leader>fp` | Open `/host-proc/` |
| `<leader>fl` | Open `/host/var/log/` |
| `Ctrl+h/j/k/l` | Window navigation |
| `Alt+j/k` | Move lines up/down |
| `Esc Esc` | Clear search highlight |
