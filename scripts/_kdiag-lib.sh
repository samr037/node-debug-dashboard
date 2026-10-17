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
# Same rule as app/httpclient.kubernetes_api_base(): the injected service IP
# (bracketed when IPv6, port 443 when unset), else the DNS name, which only
# resolves from a hostNetwork pod with ClusterFirstWithHostNet and CoreDNS up.
_k8s_api_base() {
    local host="${KUBERNETES_SERVICE_HOST:-}" port="${KUBERNETES_SERVICE_PORT:-}"
    if [ -z "$host" ]; then
        echo "https://kubernetes.default.svc"
        return
    fi
    case "$host" in
        \[*) ;;
        *:*) host="[${host}]" ;;
    esac
    echo "https://${host}:${port:-443}"
}
K8S_API="$(_k8s_api_base)"
NODE_NAME="${KUBERNETES_NODE_NAME:-$(hostname)}"
HOST_PROC="${HOST_PROC:-/host-proc}"

header() { echo -e "\n${C_BOLD}${C_CYAN}── $1 ──${C_RESET}\n"; }
warn()   { echo -e "  ${C_YELLOW}⚠${C_RESET} $1"; }
ok()     { echo -e "  ${C_GREEN}✓${C_RESET} $1"; }
fail()   { echo -e "  ${C_RED}✗${C_RESET} $1"; }
dim()    { echo -e "  ${C_DIM}$1${C_RESET}"; }

# Authenticated curl to K8s API, no timeout (watch streams use it directly)
# Usage: k8s_curl "/api/v1/nodes" [extra-curl-args...]
# The token goes in via stdin (-H @-), never on the command line: with hostPID
# /proc/<pid>/cmdline is visible node-wide and served by /api/processes.
# Verifies the API server against the SA CA; on failure prints a message on
# stderr and returns curl's exit code, so callers use `x=$(...) || return 0`.
k8s_curl() {
    local path="$1"; shift
    local token
    token=$(cat "$K8S_TOKEN_FILE" 2>/dev/null) || true
    if [ -z "$token" ]; then
        echo '{"error":"no service account token"}'
        fail "No service account token (${K8S_TOKEN_FILE})" >&2
        return 1
    fi
    if [ ! -f "$K8S_CA" ]; then
        echo '{"error":"no service account CA"}'
        fail "No service account CA (${K8S_CA}); refusing to send the token unverified" >&2
        return 1
    fi
    local rc=0
    printf 'Authorization: Bearer %s\n' "$token" | \
        curl -s --cacert "$K8S_CA" -H @- "${K8S_API}${path}" "$@" 2>/dev/null || rc=$?
    [ "$rc" -eq 0 ] || fail "K8s API unreachable: ${path} (curl exit ${rc})" >&2
    return "$rc"
}

# Authenticated curl to K8s API (10s timeout)
# Usage: k8s_api "/api/v1/nodes" [extra-curl-args...]
k8s_api() {
    local path="$1"; shift
    k8s_curl "$path" --max-time 10 "$@"
}

# Resolve etcd cert paths (Talos vs kubeadm)
_resolve_etcd_certs() {
    # Talos: /host/system/secrets/etcd/
    if [ -f "/host/system/secrets/etcd/ca.crt" ]; then
        ETCD_CA="/host/system/secrets/etcd/ca.crt"
        ETCD_CERT="/host/system/secrets/etcd/admin.crt"
        ETCD_KEY="/host/system/secrets/etcd/admin.key"
        return 0
    fi
    # kubeadm: /host/etc/kubernetes/pki/etcd/
    if [ -f "/host/etc/kubernetes/pki/etcd/ca.crt" ]; then
        ETCD_CA="/host/etc/kubernetes/pki/etcd/ca.crt"
        ETCD_CERT="/host/etc/kubernetes/pki/etcd/server.crt"
        ETCD_KEY="/host/etc/kubernetes/pki/etcd/server.key"
        return 0
    fi
    return 1
}

# Etcd API via the host etcd (CP nodes only)
etcd_api() {
    local path="$1"; shift

    if ! _resolve_etcd_certs; then
        echo '{"error":"not a control plane node or etcd certs not found"}'
        return 1
    fi

    # Verified against the etcd CA (Talos and kubeadm server certs cover 127.0.0.1)
    local rc=0
    curl -s --max-time 10 \
        --cert "$ETCD_CERT" \
        --key "$ETCD_KEY" \
        --cacert "$ETCD_CA" \
        "https://127.0.0.1:2379${path}" "$@" 2>/dev/null || rc=$?
    if [ "$rc" -ne 0 ]; then
        if [ "$(readlink /proc/self/ns/net)" != "$(readlink /host-proc/1/ns/net 2>/dev/null || readlink /proc/self/ns/net)" ]; then
            fail "etcd unreachable: this pod is not on the host network (hostNetwork=false), so 127.0.0.1:2379 is the pod itself" >&2
        else
            fail "etcd unreachable: ${path} (curl exit ${rc})" >&2
        fi
    fi
    return "$rc"
}

# jq helper: Kubernetes quantity ("500m", "512M", "1Gi", "128974848", "1e3")
# to a plain number. Unparseable values count as 0 instead of aborting jq.
# Usage: jq "${JQ_QTY}"'[.items[].spec.containers[].resources.requests.memory // "0" | qty] | add'
JQ_QTY='def qty: tostring | (try (capture("^(?<n>[+-]?[0-9.]+([eE][+-]?[0-9]+)?)(?<s>[a-zA-Z]*)$")
    | (.n | tonumber) * ({"": 1, "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "M": 1e6, "G": 1e9,
        "T": 1e12, "P": 1e15, "E": 1e18, "Ki": 1024, "Mi": 1048576, "Gi": 1073741824,
        "Ti": 1099511627776, "Pi": 1125899906842624, "Ei": 1152921504606846976}[.s] // 0)) catch 0) // 0; '

# Check if this is a control plane node
is_control_plane() {
    [ -f "/host/system/secrets/etcd/ca.crt" ] || \
    [ -f "/host/etc/kubernetes/pki/etcd/ca.crt" ] || \
    [ -f "/host/etc/kubernetes/manifests/kube-apiserver.yaml" ]
}

# Resolve K8s PKI cert directory
K8S_PKI_DIR=""
if [ -d "/host/system/secrets/kubernetes" ]; then
    K8S_PKI_DIR="/host/system/secrets/kubernetes"
elif [ -d "/host/etc/kubernetes/pki" ]; then
    K8S_PKI_DIR="/host/etc/kubernetes/pki"
fi

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
