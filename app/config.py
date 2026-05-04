import os

HOST_ROOT = os.environ.get("HOST_ROOT", "/host")
HOST_PROC = os.environ.get("HOST_PROC", "/host-proc")
CACHE_TTL = int(os.environ.get("CACHE_TTL", "10"))
COMMAND_TIMEOUT = float(os.environ.get("COMMAND_TIMEOUT", "10"))

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
