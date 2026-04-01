#!/bin/bash
# Shared library for kdiag-* scripts
# Sourced by each kdiag tool — not executed directly

# ── Colors ──
C_RESET="\033[0m"
C_BOLD="\033[1m"
C_DIM="\033[2m"
C_CYAN="\033[36m"
C_GREEN="\033[32m"
C_YELLOW="\033[33m"
C_RED="\033[31m"
C_BLUE="\033[34m"
C_MAGENTA="\033[35m"
C_WHITE="\033[97m"

# ── K8s API ──
K8S_SA_DIR="/var/run/secrets/kubernetes.io/serviceaccount"
K8S_CA="${K8S_SA_DIR}/ca.crt"
K8S_TOKEN_FILE="${K8S_SA_DIR}/token"
K8S_API="https://kubernetes.default.svc"
NODE_NAME="${KUBERNETES_NODE_NAME:-$(hostname)}"
HOST_PROC="${HOST_PROC:-/host-proc}"

header() { echo -e "\n${C_BOLD}${C_CYAN}── $1 ──${C_RESET}\n"; }
warn()   { echo -e "  ${C_YELLOW}⚠${C_RESET} $1"; }
ok()     { echo -e "  ${C_GREEN}✓${C_RESET} $1"; }
fail()   { echo -e "  ${C_RED}✗${C_RESET} $1"; }
dim()    { echo -e "  ${C_DIM}$1${C_RESET}"; }

# Authenticated curl to K8s API
# Usage: k8s_api "/api/v1/nodes" [extra-curl-args...]
k8s_api() {
    local path="$1"; shift
    local token
    token=$(cat "$K8S_TOKEN_FILE" 2>/dev/null)
    if [ -z "$token" ]; then
        echo '{"error":"no service account token"}'
        return 1
    fi
    curl -sk --max-time 10 \
        --cacert "$K8S_CA" \
        -H "Authorization: Bearer ${token}" \
        "${K8S_API}${path}" "$@" 2>/dev/null
}

# Etcd API via the host etcd (CP nodes only)
# Uses client certs from /host/etc/kubernetes/pki/etcd/
etcd_api() {
    local path="$1"; shift
    local etcd_cert="/host/etc/kubernetes/pki/etcd/server.crt"
    local etcd_key="/host/etc/kubernetes/pki/etcd/server.key"
    local etcd_ca="/host/etc/kubernetes/pki/etcd/ca.crt"

    if [ ! -f "$etcd_cert" ]; then
        echo '{"error":"not a control plane node or etcd certs not found"}'
        return 1
    fi

    curl -sk --max-time 10 \
        --cert "$etcd_cert" \
        --key "$etcd_key" \
        --cacert "$etcd_ca" \
        "https://127.0.0.1:2379${path}" "$@" 2>/dev/null
}

# Check if this is a control plane node
is_control_plane() {
    [ -f "/host/etc/kubernetes/pki/etcd/ca.crt" ] || \
    [ -f "/host/etc/kubernetes/manifests/kube-apiserver.yaml" ]
}

# Format bytes to human readable
human_bytes() {
    local bytes="${1:-0}"
    if [ "$bytes" -ge 1073741824 ] 2>/dev/null; then
        awk "BEGIN{printf \"%.1f GB\", $bytes/1073741824}"
    elif [ "$bytes" -ge 1048576 ] 2>/dev/null; then
        awk "BEGIN{printf \"%.1f MB\", $bytes/1048576}"
    elif [ "$bytes" -ge 1024 ] 2>/dev/null; then
        awk "BEGIN{printf \"%.1f KB\", $bytes/1024}"
    else
        echo "${bytes} B"
    fi
}

# Format duration from seconds to human readable
human_duration() {
    local secs="${1:-0}"
    if [ "$secs" -ge 86400 ] 2>/dev/null; then
        echo "$((secs/86400))d $((secs%86400/3600))h"
    elif [ "$secs" -ge 3600 ] 2>/dev/null; then
        echo "$((secs/3600))h $((secs%3600/60))m"
    elif [ "$secs" -ge 60 ] 2>/dev/null; then
        echo "$((secs/60))m $((secs%60))s"
    else
        echo "${secs}s"
    fi
}

# Color a percentage: green < 70, yellow < 90, red >= 90
color_pct() {
    local pct="${1:-0}"
    local num="${pct%.*}"
    if [ "${num}" -ge 90 ] 2>/dev/null; then echo -e "${C_RED}${pct}%${C_RESET}"
    elif [ "${num}" -ge 70 ] 2>/dev/null; then echo -e "${C_YELLOW}${pct}%${C_RESET}"
    else echo -e "${C_GREEN}${pct}%${C_RESET}"; fi
}

# Draw a percentage bar: [████████░░░░] 75%
pct_bar() {
    local pct="${1:-0}"
    local width="${2:-20}"
    local num="${pct%.*}"
    local filled=$((num * width / 100))
    [ "$filled" -gt "$width" ] && filled=$width
    local empty=$((width - filled))

    local color="$C_GREEN"
    [ "$num" -ge 70 ] 2>/dev/null && color="$C_YELLOW"
    [ "$num" -ge 90 ] 2>/dev/null && color="$C_RED"

    printf "[${color}"
    printf '█%.0s' $(seq 1 $filled 2>/dev/null)
    printf "${C_DIM}"
    printf '░%.0s' $(seq 1 $empty 2>/dev/null)
    printf "${C_RESET}] ${color}%s%%${C_RESET}" "$num"
}
