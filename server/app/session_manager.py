import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import SESSIONS_DIR, WORKSPACE_DIR

_sessions: dict[str, dict] = {}


def _run(args: list[str]) -> tuple[int, str]:
    r = subprocess.run(
        ["git"] + args,
        cwd=str(WORKSPACE_DIR),
        capture_output=True,
        text=True,
        timeout=30,
    )
    return r.returncode, (r.stderr.strip() or r.stdout.strip())


def create_session(
    branch: str,
    user_name: str = "anonymous",
    create_branch: bool = False,
    start_point: str = "",
) -> dict:
    session_id = str(uuid.uuid4())
    worktree_path = str(SESSIONS_DIR / session_id)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    if create_branch:
        args = ["worktree", "add", "-b", branch, worktree_path]
        if start_point:
            args.append(start_point)
    else:
        args = ["worktree", "add", worktree_path, branch]

    rc, msg = _run(args)
    if rc != 0:
        raise RuntimeError(msg or f"git worktree add failed for branch '{branch}'")

    session = {
        "id": session_id,
        "branch": branch,
        "user_name": user_name,
        "worktree_path": worktree_path,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }
    _sessions[session_id] = session
    return session


def get_session(session_id: str) -> Optional[dict]:
    return _sessions.get(session_id)


def list_sessions() -> list:
    return list(_sessions.values())


def delete_session(session_id: str) -> bool:
    session = _sessions.pop(session_id, None)
    if not session:
        return False
    subprocess.run(
        ["git", "worktree", "remove", "--force", session["worktree_path"]],
        cwd=str(WORKSPACE_DIR),
        capture_output=True,
        text=True,
        timeout=30,
    )
    return True
