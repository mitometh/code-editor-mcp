import os
from pathlib import Path

WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", "/workspace")).resolve()
SESSIONS_DIR = Path(os.environ.get("SESSIONS_DIR", "/sessions")).resolve()
API_PORT = int(os.environ.get("API_PORT", "8000"))
