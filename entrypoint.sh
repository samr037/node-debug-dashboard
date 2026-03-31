#!/bin/bash
set -e

# Generate SSH host keys if missing
ssh-keygen -A

# Install authorized keys if secret is mounted
if [ -f /root/.ssh/authorized_keys_mount/authorized_keys ]; then
    mkdir -p /root/.ssh && chmod 700 /root/.ssh
    cp /root/.ssh/authorized_keys_mount/authorized_keys /root/.ssh/authorized_keys
    chmod 600 /root/.ssh/authorized_keys

    mkdir -p /home/debug/.ssh && chmod 700 /home/debug/.ssh
    cp /root/.ssh/authorized_keys_mount/authorized_keys /home/debug/.ssh/authorized_keys
    chown -R debug:debug /home/debug/.ssh
    chmod 600 /home/debug/.ssh/authorized_keys
fi

# Convenience aliases and PATH for host access
for rcfile in /root/.bashrc /home/debug/.bashrc; do
    grep -q 'hostns' "$rcfile" 2>/dev/null || {
        echo "alias hostns='nsenter --target 1 --mount --uts --ipc --net --pid -- /bin/bash'" >> "$rcfile"
        echo "export PATH=\$PATH:/host/usr/local/bin:/host/usr/bin:/host/bin" >> "$rcfile"
    }
done

echo "======================================="
echo " Node Debug Dashboard"
echo " HTTP dashboard on port 80"
echo " SSH on port 2022 (debug/debug, root/root)"
echo " Host filesystem at /host"
echo "======================================="

# Start sshd in background
/usr/sbin/sshd -D -e -p 2022 &

# Start the dashboard API (PID 1)
cd /opt/node-dashboard
exec uvicorn app.main:app --host 0.0.0.0 --port 80 --workers 1 --log-level info
