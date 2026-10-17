#!/bin/bash
set -e

# Env booleans: 1/true/yes/on, any case, surrounding whitespace ignored —
# same rule as env_bool() in app/config.py.
is_true() {
    case "$(strip "${1,,}")" in
        1|true|yes|on) return 0 ;;
        *) return 1 ;;
    esac
}

# Trim leading/trailing whitespace (a secret mounted from a file usually
# carries a trailing newline).
strip() {
    local v="$1"
    v="${v#"${v%%[![:space:]]*}"}"
    v="${v%"${v##*[![:space:]]}"}"
    printf '%s' "$v"
}

HTTP_PORT="${HTTP_PORT:-80}"
SSH_ENABLED="${SSH_ENABLED:-false}"
SSH_PORT="${SSH_PORT:-2022}"
SSH_PASSWORD_AUTH="${SSH_PASSWORD_AUTH:-false}"
SSH_PASSWORD="$(strip "${SSH_PASSWORD:-}")"
SSH_AUTHORIZED_KEYS="${SSH_AUTHORIZED_KEYS:-}"
# Host keys are kept here when the directory exists (a hostPath mount), so a
# node keeps the same SSH identity across pod restarts and image upgrades.
# Without it, each container generates its own keys.
SSH_HOST_KEY_DIR="${SSH_HOST_KEY_DIR:-/var/lib/node-debug-dashboard/ssh}"

SSHD_PID=""

if is_true "$SSH_ENABLED"; then
    # Host keys. The image ships none (see Dockerfile), so every node gets
    # its own instead of one private key baked into a public image.
    if [ -d "$SSH_HOST_KEY_DIR" ]; then
        mkdir -p "$SSH_HOST_KEY_DIR/etc/ssh"
        chmod 700 "$SSH_HOST_KEY_DIR"
        ssh-keygen -A -f "$SSH_HOST_KEY_DIR"
        cp -p "$SSH_HOST_KEY_DIR"/etc/ssh/ssh_host_* /etc/ssh/
    else
        ssh-keygen -A
    fi

    # Configure password authentication. The image ships both accounts
    # locked, so enabling password auth without supplying one would leave
    # sshd accepting passwords that no account can satisfy.
    password_auth=no
    if is_true "$SSH_PASSWORD_AUTH"; then
        if [ -z "$SSH_PASSWORD" ]; then
            echo "SSH_PASSWORD_AUTH is set but SSH_PASSWORD is empty — refusing to enable password auth." >&2
            echo "Set SSH_PASSWORD, or use SSH_AUTHORIZED_KEYS for key-based access." >&2
            sed -i 's/^PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config.d/debug.conf
        else
            echo "debug:${SSH_PASSWORD}" | chpasswd
            sed -i 's/^PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config.d/debug.conf
            password_auth=yes
        fi
    else
        sed -i 's/^PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config.d/debug.conf
    fi

    # Install authorized keys from env var (newline-separated)
    if [ -n "$SSH_AUTHORIZED_KEYS" ]; then
        mkdir -p /root/.ssh && chmod 700 /root/.ssh
        echo "$SSH_AUTHORIZED_KEYS" > /root/.ssh/authorized_keys
        chmod 600 /root/.ssh/authorized_keys

        mkdir -p /home/debug/.ssh && chmod 700 /home/debug/.ssh
        echo "$SSH_AUTHORIZED_KEYS" > /home/debug/.ssh/authorized_keys
        chown -R debug:debug /home/debug/.ssh
        chmod 600 /home/debug/.ssh/authorized_keys
    fi

    # Install authorized keys from mounted secret (if present)
    if [ -f /root/.ssh/authorized_keys_mount/authorized_keys ]; then
        mkdir -p /root/.ssh && chmod 700 /root/.ssh
        cat /root/.ssh/authorized_keys_mount/authorized_keys >> /root/.ssh/authorized_keys 2>/dev/null || \
        cp /root/.ssh/authorized_keys_mount/authorized_keys /root/.ssh/authorized_keys
        chmod 600 /root/.ssh/authorized_keys

        mkdir -p /home/debug/.ssh && chmod 700 /home/debug/.ssh
        cat /root/.ssh/authorized_keys_mount/authorized_keys >> /home/debug/.ssh/authorized_keys 2>/dev/null || \
        cp /root/.ssh/authorized_keys_mount/authorized_keys /home/debug/.ssh/authorized_keys
        chown -R debug:debug /home/debug/.ssh
        chmod 600 /home/debug/.ssh/authorized_keys
    fi

    # sshd builds a clean environment for each login (PAM reads
    # /etc/environment), so the pod env never reaches SSH sessions: kpn
    # queried spec.nodeName= (unscheduled pods) and the MOTD said "unknown".
    # Only plain values are written; anything else is skipped rather than
    # risk a malformed line.
    : > /etc/environment
    for var in KUBERNETES_NODE_NAME HTTP_PORT LANG LC_ALL; do
        val="${!var}"
        if [[ -n "$val" && "$val" =~ ^[A-Za-z0-9._:@/+-]+$ ]]; then
            echo "${var}=${val}" >> /etc/environment
        fi
    done

    # Fail the container on a bad config instead of logging "Starting SSH"
    # and serving HTTP with no SSH behind it.
    /usr/sbin/sshd -t -p "$SSH_PORT"
    # Log what sshd will actually do, not the raw env value (which still read
    # "true" when password auth was refused for lack of a password).
    echo "Starting SSH on port ${SSH_PORT} (password=${password_auth})"
    /usr/sbin/sshd -D -e -p "$SSH_PORT" &
    SSHD_PID=$!
    # A bind failure (port taken on the node with hostNetwork) exits at once.
    sleep 1
    if ! kill -0 "$SSHD_PID" 2>/dev/null; then
        echo "sshd failed to start on port ${SSH_PORT} — see the error above." >&2
        exit 1
    fi
else
    echo "SSH disabled (SSH_ENABLED=${SSH_ENABLED})"
fi

echo "======================================="
echo " Node Debug Dashboard"
echo " HTTP dashboard on port ${HTTP_PORT}"
if [ -n "$SSHD_PID" ]; then
    echo " SSH on port ${SSH_PORT} (zsh + oh-my-zsh)"
fi
echo " Host filesystem at /host"
echo "======================================="

# Start the dashboard API
cd /opt/node-dashboard
UVICORN=(uvicorn app.main:app --host 0.0.0.0 --port "${HTTP_PORT}" --workers 1 --log-level info
         # Bound shutdown: open log WebSockets would otherwise hold it until
         # the kubelet's SIGKILL.
         --timeout-graceful-shutdown 10)

if [ -z "$SSHD_PID" ]; then
    # uvicorn is PID 1
    exec "${UVICORN[@]}"
fi

# With SSH, stay as PID 1 and supervise both: if either exits, stop the
# other and exit so the kubelet restarts the container, instead of a pod
# that stays Ready with sshd dead. bash also reaps orphaned session
# processes, which uvicorn would not.
"${UVICORN[@]}" &
UVICORN_PID=$!
stopping=""
trap 'stopping=1; kill -TERM "$UVICORN_PID" "$SSHD_PID" 2>/dev/null' TERM INT

set +e
wait -n "$UVICORN_PID" "$SSHD_PID"
rc=$?
if [ -z "$stopping" ]; then
    if ! kill -0 "$SSHD_PID" 2>/dev/null; then
        echo "sshd exited (status ${rc}) — stopping the container." >&2
    else
        echo "uvicorn exited (status ${rc}) — stopping the container." >&2
    fi
    kill -TERM "$UVICORN_PID" "$SSHD_PID" 2>/dev/null
fi
wait
if [ -n "$stopping" ]; then
    exit 0
fi
# A child that died on its own is a failure even if it exited 0.
[ "$rc" -eq 0 ] && rc=1
exit "$rc"
