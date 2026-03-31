import os

HOST_ROOT = os.environ.get("HOST_ROOT", "/host")
HOST_PROC = os.environ.get("HOST_PROC", "/host-proc")
CACHE_TTL = int(os.environ.get("CACHE_TTL", "10"))
COMMAND_TIMEOUT = float(os.environ.get("COMMAND_TIMEOUT", "10"))
