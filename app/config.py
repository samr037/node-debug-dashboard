import os

HOST_ROOT = os.environ.get("HOST_ROOT", "/host")
HOST_PROC = os.environ.get("HOST_PROC", "/host-proc")
CACHE_TTL = int(os.environ.get("CACHE_TTL", "10"))
COMMAND_TIMEOUT = float(os.environ.get("COMMAND_TIMEOUT", "10"))

# How far back to scan the kernel ring buffer for faults. Events older than
# this are dropped, so a one-off error at boot stops alerting forever.
# A week is long enough that a fault which happened while nobody was looking
# is still visible, and short enough that boot chatter on a node up for
# months does not linger.
DMESG_WINDOW_HOURS = int(os.environ.get("DMESG_WINDOW_HOURS", "168"))

# SSH configuration. Off and key-only by default; set SSH_ENABLED=true
# and pass SSH_AUTHORIZED_KEYS to enable.
SSH_ENABLED = os.environ.get("SSH_ENABLED", "false").lower() in ("true", "1", "yes")
SSH_PORT = int(os.environ.get("SSH_PORT", "2022"))
SSH_PASSWORD_AUTH = os.environ.get("SSH_PASSWORD_AUTH", "false").lower() in (
    "true",
    "1",
    "yes",
)
SSH_AUTHORIZED_KEYS = os.environ.get("SSH_AUTHORIZED_KEYS", "")  # newline-separated

# Dashboard authentication. Unset by default, which leaves the dashboard
# open exactly as before — this runs with hostNetwork, so an open dashboard
# is reachable by anything that can route to the node, and a full read of
# the node is one request away. Set either of these to require credentials.
#
#   AUTH_TOKEN     accepted as "Authorization: Bearer <token>"
#   AUTH_PASSWORD  accepted as HTTP Basic, so a browser can prompt for it
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "")
AUTH_USERNAME = os.environ.get("AUTH_USERNAME", "debug")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "")
