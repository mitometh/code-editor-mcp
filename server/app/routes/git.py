import subprocess
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse

from ..config import WORKSPACE_DIR
from ..models import (
    GitAddRequest,
    GitCheckoutRequest,
    GitCommitRequest,
    GitFetchRequest,
    GitPullRequest,
    GitPushRequest,
    GitResetRequest,
    GitStashRequest,
)
from ..session_manager import get_session as _get_session
from ..utils import _cat_n

router = APIRouter(prefix="/git")


def _workspace(session_id: Optional[str] = Query(None)) -> Path:
    """Resolve the active workspace: session worktree or default WORKSPACE_DIR."""
    if session_id:
        s = _get_session(session_id)
        if not s:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
        return Path(s["worktree_path"])
    return WORKSPACE_DIR


def _git(args: list[str], timeout: int = 30, cwd: str = None) -> str:
    """Run a git command in the workspace; raise HTTPException on failure."""
    try:
        r = subprocess.run(
            ["git"] + args,
            cwd=cwd or str(WORKSPACE_DIR),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if r.returncode != 0:
            raise HTTPException(
                status_code=400,
                detail=r.stderr.strip() or f"git {args[0]} failed (exit {r.returncode})",
            )
        return r.stdout
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="Git command timed out")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="git not found in container")


# ── Git status ────────────────────────────────────────────────────────────────

@router.get("/status")
def git_status(workspace: Path = Depends(_workspace)):
    """Working tree status — staged, unstaged, and untracked files."""
    raw = _git(["status", "--porcelain=v1", "--untracked-files=all"], cwd=str(workspace))
    files = []
    for line in raw.splitlines():
        if not line:
            continue
        xy, path = line[:2], line[3:]
        if " -> " in path:
            old, new = path.split(" -> ", 1)
            files.append({"xy": xy, "path": new, "orig_path": old})
        else:
            files.append({"xy": xy, "path": path})
    return {"files": files, "summary": _git(["status", "--short"], cwd=str(workspace))}


# ── Git diff ──────────────────────────────────────────────────────────────────

@router.get("/diff", response_class=PlainTextResponse)
def git_diff(
    path: str = Query("", description="Restrict diff to this file/directory"),
    ref: str = Query("", description="Ref to diff against, e.g. HEAD, main, <hash>"),
    staged: bool = Query(False, description="Show staged (indexed) changes"),
    stat: bool = Query(False, description="Show diffstat summary instead of full patch"),
    workspace: Path = Depends(_workspace),
):
    """Unified diff of changes in the working tree (or staged area)."""
    args = ["diff"]
    if stat:
        args.append("--stat")
    if staged:
        args.append("--staged")
    if ref:
        args.append(ref)
    if path:
        args += ["--", path]
    return _git(args, cwd=str(workspace))


# ── Git diff for a specific commit ───────────────────────────────────────────

@router.get("/diff/{commit_hash}", response_class=PlainTextResponse)
def git_diff_commit(
    commit_hash: str,
    workspace: Path = Depends(_workspace),
):
    """Unified diff introduced by a specific commit (commit vs its parent)."""
    return _git(["diff", f"{commit_hash}^", commit_hash], cwd=str(workspace))


# ── Git log ───────────────────────────────────────────────────────────────────

@router.get("/log")
def git_log(
    max_count: int = Query(20, ge=1, le=500),
    path: str = Query("", description="Only commits touching this path"),
    ref: str = Query("HEAD", description="Branch, tag, or commit to start from"),
    oneline: bool = Query(False, description="Compact one-line format"),
    workspace: Path = Depends(_workspace),
):
    """Commit history with hash, author, date, and message."""
    fmt = "--oneline" if oneline else "--pretty=format:%H%x09%an%x09%ai%x09%s"
    args = ["log", fmt, f"--max-count={max_count}", ref]
    if path:
        args += ["--", path]

    raw = _git(args, cwd=str(workspace))
    if oneline:
        return {"log": raw}

    commits = []
    for line in raw.splitlines():
        parts = line.split("\t", 3)
        if len(parts) == 4:
            commits.append({"hash": parts[0], "author": parts[1], "date": parts[2], "subject": parts[3]})
    return {"commits": commits}


# ── Git tree ──────────────────────────────────────────────────────────────────

@router.get("/tree")
def git_tree(
    path: str = Query("", description="Subdirectory to list"),
    ref: str = Query("HEAD", description="Commit, branch, or tag"),
    recursive: bool = Query(True, description="Recurse into subdirectories"),
    workspace: Path = Depends(_workspace),
):
    """List all files tracked by git at the given ref, plus untracked and staged-new files."""
    prefix = path.lstrip("/")
    cwd = str(workspace)

    tracked: set[str] = set()
    try:
        args = ["ls-tree", "--name-only"]
        if recursive:
            args.append("-r")
        args.append(ref)
        if prefix:
            args.append(prefix)
        raw = _git(args, cwd=cwd)
        tracked = {f for f in raw.splitlines() if f}
    except HTTPException:
        pass

    extra: set[str] = set()
    try:
        status_raw = _git(["status", "--porcelain=v1", "--untracked-files=all"], cwd=cwd)
        for line in status_raw.splitlines():
            if not line:
                continue
            xy = line[:2]
            file_path = line[3:].split(" -> ")[-1]
            if xy in ("??", "A ") and file_path not in tracked:
                if not prefix or file_path.startswith(prefix):
                    extra.add(file_path)
    except HTTPException:
        pass

    files = sorted(tracked | extra)
    return {"files": files, "ref": ref}


# ── Git show ──────────────────────────────────────────────────────────────────

@router.get("/show", response_class=PlainTextResponse)
def git_show(
    path: str = Query(..., description="File path relative to workspace root"),
    ref: str = Query("HEAD", description="Commit, branch, or tag"),
    line_numbers: bool = Query(True, description="Prefix lines with line numbers"),
    workspace: Path = Depends(_workspace),
):
    """Return the content of a file as it exists at a given git ref."""
    content = _git(["show", f"{ref}:{path.lstrip('/')}"], cwd=str(workspace))
    if not line_numbers:
        return content
    lines = content.splitlines(keepends=True)
    return _cat_n(lines, 1)


# ── Git blame ─────────────────────────────────────────────────────────────────

@router.get("/blame", response_class=PlainTextResponse)
def git_blame(
    path: str = Query(..., description="File path relative to workspace root"),
    ref: str = Query("HEAD"),
    workspace: Path = Depends(_workspace),
):
    """Show which commit and author last modified each line of a file."""
    return _git(["blame", ref, "--", path.lstrip("/")], cwd=str(workspace))


# ── Git branches ─────────────────────────────────────────────────────────────

@router.get("/branches")
def git_branches(
    all: bool = Query(False, description="Include remote-tracking branches"),
    workspace: Path = Depends(_workspace),
):
    """List local (and optionally remote) branches with their latest commit."""
    args = ["branch", "-v"]
    if all:
        args.append("-a")
    raw = _git(args, cwd=str(workspace))
    branches = []
    for line in raw.splitlines():
        current = line.startswith("*")
        parts = line.lstrip("* ").split(None, 2)
        branches.append({
            "name": parts[0] if parts else "",
            "hash": parts[1] if len(parts) > 1 else "",
            "subject": parts[2] if len(parts) > 2 else "",
            "current": current,
        })
    return {"branches": branches}


# ── Git add ───────────────────────────────────────────────────────────────────

@router.post("/add")
def git_add(
    req: GitAddRequest,
    workspace: Path = Depends(_workspace),
):
    """Stage files for the next commit."""
    paths = req.paths if req.paths else ["."]
    _git(["add"] + paths, cwd=str(workspace))
    return {"message": f"Staged: {', '.join(paths)}"}


# ── Git commit ────────────────────────────────────────────────────────────────

@router.post("/commit")
def git_commit(
    req: GitCommitRequest,
    workspace: Path = Depends(_workspace),
):
    """Create a commit with the staged changes."""
    args = ["commit", "-m", req.message]
    if req.author:
        args += ["--author", req.author]
    output = _git(args, cwd=str(workspace))
    return {"message": output.strip()}


# ── Git checkout ──────────────────────────────────────────────────────────────

@router.post("/checkout")
def git_checkout(
    req: GitCheckoutRequest,
    workspace: Path = Depends(_workspace),
):
    """Switch to a branch or commit, optionally creating a new branch."""
    args = ["checkout"]
    if req.create:
        args.append("-b")
    args.append(req.ref)
    output = _git(args, cwd=str(workspace))
    return {"message": output.strip() or f"Switched to '{req.ref}'"}


# ── Git push ──────────────────────────────────────────────────────────────────

@router.post("/push")
def git_push(
    req: GitPushRequest,
    workspace: Path = Depends(_workspace),
):
    """Push commits to a remote repository."""
    args = ["push"]
    if req.force:
        args.append("--force-with-lease")
    args.append(req.remote)
    if req.branch:
        args.append(req.branch)
    output = _git(args, timeout=60, cwd=str(workspace))
    return {"message": output.strip() or "Pushed successfully"}


# ── Git pull ──────────────────────────────────────────────────────────────────

@router.post("/pull")
def git_pull(
    req: GitPullRequest,
    workspace: Path = Depends(_workspace),
):
    """Pull and merge changes from a remote repository."""
    args = ["pull", req.remote]
    if req.branch:
        args.append(req.branch)
    output = _git(args, timeout=60, cwd=str(workspace))
    return {"message": output.strip()}


# ── Git fetch ─────────────────────────────────────────────────────────────────

@router.post("/fetch")
def git_fetch(
    req: GitFetchRequest,
    workspace: Path = Depends(_workspace),
):
    """Fetch changes from a remote without merging."""
    args = ["fetch", req.remote]
    if req.prune:
        args.append("--prune")
    output = _git(args, timeout=60, cwd=str(workspace))
    return {"message": output.strip() or "Fetched successfully"}


# ── Git stash ─────────────────────────────────────────────────────────────────

@router.post("/stash")
def git_stash(
    req: GitStashRequest,
    workspace: Path = Depends(_workspace),
):
    """Save or restore stashed working tree changes.

    action: push | pop | list | drop
    """
    args = ["stash", req.action]
    if req.action == "push" and req.message:
        args += ["-m", req.message]
    output = _git(args, cwd=str(workspace))
    return {"message": output.strip()}


# ── Git reset ─────────────────────────────────────────────────────────────────

@router.post("/reset")
def git_reset(
    req: GitResetRequest,
    workspace: Path = Depends(_workspace),
):
    """Reset HEAD or unstage specific files.

    mode: soft | mixed | hard  (ignored when paths are provided)
    """
    if req.paths:
        args = ["reset", req.ref, "--"] + req.paths
    else:
        args = ["reset", f"--{req.mode}", req.ref]
    output = _git(args, cwd=str(workspace))
    return {"message": output.strip() or f"Reset to {req.ref}"}
