#!/bin/bash
set -e

SSH_ENABLED="${SSH_ENABLED:-true}"
SSH_PORT="${SSH_PORT:-2022}"
SSH_PASSWORD_AUTH="${SSH_PASSWORD_AUTH:-true}"
SSH_AUTHORIZED_KEYS="${SSH_AUTHORIZED_KEYS:-}"

# Convenience aliases and PATH for host access
for rcfile in /root/.bashrc /home/debug/.bashrc; do
    grep -q 'hostns' "$rcfile" 2>/dev/null || {
        echo "alias hostns='nsenter --target 1 --mount --uts --ipc --net --pid -- /bin/bash'" >> "$rcfile"
        echo "export PATH=\$PATH:/host/usr/local/bin:/host/usr/bin:/host/bin" >> "$rcfile"
    }
done

if [ "$SSH_ENABLED" = "true" ] || [ "$SSH_ENABLED" = "1" ] || [ "$SSH_ENABLED" = "yes" ]; then
    # Generate SSH host keys if missing
    ssh-keygen -A

    # Configure password authentication
    if [ "$SSH_PASSWORD_AUTH" = "true" ] || [ "$SSH_PASSWORD_AUTH" = "1" ] || [ "$SSH_PASSWORD_AUTH" = "yes" ]; then
        sed -i 's/^PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config.d/debug.conf
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

    echo "Starting SSH on port ${SSH_PORT} (password=${SSH_PASSWORD_AUTH})"
    /usr/sbin/sshd -D -e -p "$SSH_PORT" &
else
    echo "SSH disabled (SSH_ENABLED=${SSH_ENABLED})"
fi

echo "======================================="
echo " Node Debug Dashboard"
echo " HTTP dashboard on port 80"
[ "$SSH_ENABLED" = "true" ] && echo " SSH on port ${SSH_PORT}"
echo " Host filesystem at /host"
echo "======================================="

# Start the dashboard API (PID 1)
cd /opt/node-dashboard
exec uvicorn app.main:app --host 0.0.0.0 --port 80 --workers 1 --log-level info
