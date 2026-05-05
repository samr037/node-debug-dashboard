#!/bin/bash
# Dynamic MOTD for Node Debug Dashboard SSH sessions
# Runs on each login — reads live node data

# Colors
C_RESET="\033[0m"
C_BOLD="\033[1m"
C_DIM="\033[2m"
C_CYAN="\033[36m"
C_GREEN="\033[32m"
C_YELLOW="\033[33m"
C_BLUE="\033[34m"
C_MAGENTA="\033[35m"
C_WHITE="\033[97m"
C_RED="\033[31m"
C_BG_BLUE="\033[44m"

# ASCII art
cat << 'LOGO'

    ╔═══════════════════════════════════════════════════════╗
    ║                                                       ║
    ║   ███╗   ██╗██████╗ ██████╗                           ║
    ║   ████╗  ██║██╔══██╗██╔══██╗                          ║
    ║   ██╔██╗ ██║██║  ██║██████╔╝                          ║
    ║   ██║╚██╗██║██║  ██║██╔══██╗                          ║
    ║   ██║ ╚████║██████╔╝██████╔╝                          ║
    ║   ╚═╝  ╚═══╝╚═════╝ ╚═════╝                          ║
    ║                                                       ║
    ║        Node Debug Dashboard — SSH Shell                ║
    ╚═══════════════════════════════════════════════════════╝

LOGO

# Gather node info
HOSTNAME=$(cat /host/etc/hostname 2>/dev/null || hostname)
KERNEL=$(cat /host-proc/version 2>/dev/null | awk '{print $3}' || uname -r)
UPTIME=$(awk '{d=int($1/86400); h=int(($1%86400)/3600); m=int(($1%3600)/60); printf "%dd %dh %dm", d, h, m}' /host-proc/uptime 2>/dev/null || uptime -p)

# CPU info
CPU_MODEL=$(grep -m1 'model name' /host-proc/cpuinfo 2>/dev/null | cut -d: -f2 | xargs)
CPU_CORES=$(grep -c ^processor /host-proc/cpuinfo 2>/dev/null || echo "?")
LOAD=$(awk '{printf "%.2f %.2f %.2f", $1, $2, $3}' /host-proc/loadavg 2>/dev/null || echo "N/A")

# Memory
MEM_INFO=$(awk '/MemTotal/{t=$2} /MemAvailable/{a=$2} END{printf "%.1f/%.1fGB (%.0f%% used)", (t-a)/1048576, t/1048576, ((t-a)/t)*100}' /host-proc/meminfo 2>/dev/null || echo "N/A")

# IPs
NODE_IPS=$(ip -4 addr show 2>/dev/null | grep 'inet ' | grep -v '127.0.0.1' | awk '{print $2}' | cut -d/ -f1 | head -3 | tr '\n' ' ')

# Kubernetes
K8S_NODE="${KUBERNETES_NODE_NAME:-unknown}"
K8S_ROLE="worker"
if [ -f /host/etc/kubernetes/manifests/kube-apiserver.yaml ] || [ -d /host/etc/kubernetes/manifests ] && ls /host/etc/kubernetes/manifests/etcd* &>/dev/null; then
    K8S_ROLE="control-plane"
fi

# GPU
GPU_INFO=""
if nsenter --target 1 --mount --uts --ipc --net --pid -- nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | read -r gpu_line; then
    GPU_INFO="$gpu_line"
fi

# Dashboard URL — prefer 192.168.x IPs over link-local/pod CIDRs
DASH_IP=$(ip -4 addr show 2>/dev/null | grep 'inet ' | grep -v '127.0.0.1' | awk '{print $2}' | cut -d/ -f1 | grep '^192\.168\.' | head -1)
[ -z "$DASH_IP" ] && DASH_IP=$(ip -4 addr show 2>/dev/null | grep 'inet ' | grep -v '127.0.0.1' | grep -v '169.254' | grep -v '10.244' | awk '{print $2}' | cut -d/ -f1 | head -1)

# Print node info
echo -e "${C_BOLD}${C_CYAN}  Node${C_RESET}        ${C_WHITE}${HOSTNAME}${C_RESET} ${C_DIM}(${K8S_NODE})${C_RESET}"
echo -e "${C_BOLD}${C_CYAN}  Role${C_RESET}        ${C_WHITE}${K8S_ROLE}${C_RESET}"
echo -e "${C_BOLD}${C_CYAN}  IPs${C_RESET}         ${C_WHITE}${NODE_IPS}${C_RESET}"
echo -e "${C_BOLD}${C_CYAN}  Kernel${C_RESET}      ${C_WHITE}${KERNEL}${C_RESET}"
echo -e "${C_BOLD}${C_CYAN}  Uptime${C_RESET}      ${C_WHITE}${UPTIME}${C_RESET}"
echo -e "${C_BOLD}${C_CYAN}  CPU${C_RESET}         ${C_WHITE}${CPU_MODEL} (${CPU_CORES} cores)${C_RESET}"
echo -e "${C_BOLD}${C_CYAN}  Memory${C_RESET}      ${C_WHITE}${MEM_INFO}${C_RESET}"
echo -e "${C_BOLD}${C_CYAN}  Load${C_RESET}        ${C_WHITE}${LOAD}${C_RESET}"
[ -n "$GPU_INFO" ] && echo -e "${C_BOLD}${C_CYAN}  GPU${C_RESET}         ${C_WHITE}${GPU_INFO}${C_RESET}"
echo ""

# Quick guide
echo -e "${C_BOLD}${C_BG_BLUE}${C_WHITE} Quick Guide ${C_RESET}"
echo ""
echo -e "  ${C_GREEN}ndiag-cpu${C_RESET}       CPU diagnostics     ${C_DIM}(top, freq, load, throttle)${C_RESET}"
echo -e "  ${C_GREEN}ndiag-mem${C_RESET}       Memory diagnostics  ${C_DIM}(usage, top, dimms, swap, oom)${C_RESET}"
echo -e "  ${C_GREEN}ndiag-net${C_RESET}       Network diagnostics ${C_DIM}(ifaces, conns, dns, capture)${C_RESET}"
echo -e "  ${C_GREEN}ndiag-disk${C_RESET}      Disk diagnostics    ${C_DIM}(smart, io, bench, health)${C_RESET}"
echo -e "  ${C_GREEN}ndiag-part${C_RESET}      Partition info       ${C_DIM}(mounts, lvm, fs, usage)${C_RESET}"
echo ""
echo -e "  ${C_BLUE}kdiag-node${C_RESET}      K8s node health     ${C_DIM}(status, resources, taints, pressure)${C_RESET}"
echo -e "  ${C_BLUE}kdiag-pods${C_RESET}      Pod diagnostics     ${C_DIM}(sick, resources, images, logs)${C_RESET}"
echo -e "  ${C_BLUE}kdiag-etcd${C_RESET}      etcd deep dive      ${C_DIM}(health, size, perf, keys) [CP only]${C_RESET}"
echo -e "  ${C_BLUE}kdiag-certs${C_RESET}     Certificate audit   ${C_DIM}(k8s, etcd, SA token, TLS endpoints)${C_RESET}"
echo -e "  ${C_BLUE}kdiag-services${C_RESET}  Service debugging   ${C_DIM}(dns, endpoints, connectivity)${C_RESET}"
echo -e "  ${C_BLUE}kdiag-events${C_RESET}    Event viewer        ${C_DIM}(node, warnings, watch, ns)${C_RESET}"
echo ""
echo -e "  ${C_YELLOW}hostns${C_RESET}          Enter host namespace (full host access)"
echo -e "  ${C_YELLOW}cps${C_RESET}             List running containers (crictl)"
echo -e "  ${C_YELLOW}kn${C_RESET}              Cluster nodes"
echo -e "  ${C_YELLOW}kp${C_RESET}              All pods"
echo ""
echo -e "  ${C_MAGENTA}Dashboard${C_RESET}     ${C_BLUE}http://${DASH_IP:-<node-ip>}/${C_RESET}"
echo -e "  ${C_MAGENTA}API Docs${C_RESET}      ${C_BLUE}http://${DASH_IP:-<node-ip>}/docs${C_RESET}"
echo -e "  ${C_MAGENTA}SSH Docs${C_RESET}      ${C_BLUE}https://github.com/samr037/node-debug-dashboard/blob/main/docs/ssh.md${C_RESET}"
echo ""
echo -e "  ${C_DIM}Type 'help' for the full guide, 'ndiag-test' to run all tests${C_RESET}"
echo ""
