import os

HOST_ROOT = os.environ.get("HOST_ROOT", "/host")
HOST_PROC = os.environ.get("HOST_PROC", "/host-proc")
# The container's own /sys already resolves to the host's when privileged,
# so this is the host view in practice. Named so collectors can say which
# view they mean, and so it can be pointed elsewhere for testing.
HOST_SYS = os.environ.get("HOST_SYS", "/sys")
CACHE_TTL = int(os.environ.get("CACHE_TTL", "10"))
COMMAND_TIMEOUT = float(os.environ.get("COMMAND_TIMEOUT", "10"))

# How far back to scan the kernel ring buffer for faults. Events older than
# this are dropped, so a one-off error at boot stops alerting forever.
# A week is long enough that a fault which happened while nobody was looking
# is still visible, and short enough that boot chatter on a node up for
# months does not linger.
DMESG_WINDOW_HOURS = int(os.environ.get("DMESG_WINDOW_HOURS", "168"))

# Same idea for the lifetime error counters (NIC CRC/carrier, EDAC ECC):
# they only reset on reboot, so a counter that has not moved for this long
# is reported as info rather than staying critical for the whole uptime.
COUNTER_WINDOW_HOURS = int(os.environ.get("COUNTER_WINDOW_HOURS", "24"))


def env_bool(name: str, default: str = "false") -> bool:
    """Same truthy set as entrypoint.sh: 1/true/yes/on, any case."""
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


# SSH configuration. Off and key-only by default; set SSH_ENABLED=true
# and pass SSH_AUTHORIZED_KEYS to enable.
SSH_ENABLED = env_bool("SSH_ENABLED")
SSH_PORT = int(os.environ.get("SSH_PORT", "2022"))
# The entrypoint refuses password auth when SSH_PASSWORD is empty, so report
# what sshd actually does rather than what was asked for.
SSH_PASSWORD_AUTH = env_bool("SSH_PASSWORD_AUTH") and bool(
    os.environ.get("SSH_PASSWORD", "").strip()
)
SSH_AUTHORIZED_KEYS = os.environ.get("SSH_AUTHORIZED_KEYS", "")  # newline-separated

# Dashboard authentication. Unset by default, which leaves the dashboard
# open exactly as before — this runs with hostNetwork, so an open dashboard
# is reachable by anything that can route to the node, and a full read of
# the node is one request away. Set either of these to require credentials.
#
#   AUTH_TOKEN     accepted as "Authorization: Bearer <token>"
#   AUTH_PASSWORD  accepted as HTTP Basic, so a browser can prompt for it
#
# Stripped: a Secret built with --from-file usually ends in a newline, which
# no client can ever send back, so every login would silently fail.
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "").strip()
AUTH_USERNAME = os.environ.get("AUTH_USERNAME", "debug").strip()
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "").strip()
