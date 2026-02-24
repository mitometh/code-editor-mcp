import os
import re
import shutil
import subprocess
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", "/workspace")).resolve()
API_PORT = int(os.environ.get("API_PORT", "8000"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Remote File Server")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Path safety ───────────────────────────────────────────────────────────────

def safe_path(raw: str) -> Path:
    """Resolve path and ensure it stays within WORKSPACE_DIR."""
    resolved = (WORKSPACE_DIR / raw.lstrip("/")).resolve()
    if not str(resolved).startswith(str(WORKSPACE_DIR)):
        raise HTTPException(status_code=400, detail=f"Path '{raw}' escapes workspace root")
    return resolved


# ── Request models ────────────────────────────────────────────────────────────

class WriteRequest(BaseModel):
    file_path: str
    content: str


class EditRequest(BaseModel):
    file_path: str
    old_string: str
    new_string: str
    replace_all: bool = False


class GrepRequest(BaseModel):
    pattern: str
    path: str = ""
    glob: str = ""
    output_mode: str = "files_with_matches"  # content | files_with_matches | count
    context: int = 0       # lines before AND after (-C)
    context_before: int = 0  # -B
    context_after: int = 0   # -A
    case_insensitive: bool = False
    line_numbers: bool = True
    head_limit: int = 0
    multiline: bool = False


class BashRequest(BaseModel):
    command: str
    timeout: int = 120000  # milliseconds


class MoveRequest(BaseModel):
    source: str
    destination: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _cat_n(lines: list[str], start_line: int) -> str:
    """Format lines with cat-n style line numbers matching Claude Code's Read output."""
    out = []
    for i, line in enumerate(lines):
        out.append(f"{start_line + i:>6}\u2192{line}")
    return "".join(out)


def _collect_files(base: Path, glob_filter: str) -> list[Path]:
    pattern = glob_filter if glob_filter else "**/*"
    return sorted(
        (p for p in base.glob(pattern) if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


# ── Read ──────────────────────────────────────────────────────────────────────

@app.get("/read_file", response_class=PlainTextResponse)
def read_file(
    file_path: str = Query(...),
    offset: int = Query(1, ge=1, description="1-based line to start reading from"),
    limit: int = Query(0, ge=0, description="Max lines to read (0 = all remaining)"),
):
    target = safe_path(file_path)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")
    if not target.is_file():
        raise HTTPException(status_code=400, detail=f"Not a file: {file_path}")

    lines = target.read_text(errors="replace").splitlines(keepends=True)
    total = len(lines)
    start = offset - 1
    end = (start + limit) if limit > 0 else total
    selected = lines[start:end]

    if not selected:
        return f"(empty — file has {total} lines, offset={offset})"
    return _cat_n(selected, offset)


# ── Write ─────────────────────────────────────────────────────────────────────

@app.post("/write_file")
def write_file(req: WriteRequest):
    target = safe_path(req.file_path)
    logger.info("Write  file_path=%s  ts=%s", req.file_path, datetime.utcnow().isoformat())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(req.content)
    return {"message": f"File written: {req.file_path}"}


# ── Edit (exact string replace, unique by default) ────────────────────────────

@app.post("/edit")
def edit(req: EditRequest):
    target = safe_path(req.file_path)
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {req.file_path}")

    content = target.read_text(errors="replace")
    count = content.count(req.old_string)

    if count == 0:
        raise HTTPException(status_code=400, detail="old_string not found in file")
    if not req.replace_all and count > 1:
        raise HTTPException(
            status_code=400,
            detail=f"old_string matches {count} locations — must be unique "
                   f"(or pass replace_all=true to replace all)",
        )

    n = None if req.replace_all else 1
    new_content = content.replace(req.old_string, req.new_string, n) if n else content.replace(req.old_string, req.new_string)
    target.write_text(new_content)
    replaced = count if req.replace_all else 1
    return {"message": f"Replaced {replaced} occurrence(s) in {req.file_path}"}


# ── Glob ──────────────────────────────────────────────────────────────────────

@app.get("/glob")
def glob_files(
    pattern: str = Query(..., description="Glob pattern e.g. **/*.py"),
    path: str = Query("", description="Base directory relative to /workspace"),
):
    base = safe_path(path) if path else WORKSPACE_DIR
    if not base.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")
    try:
        matches = sorted(
            (p for p in base.glob(pattern) if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid glob pattern: {e}")

    return {"matches": [str(p.relative_to(WORKSPACE_DIR)) for p in matches]}


# ── Grep ──────────────────────────────────────────────────────────────────────

@app.post("/grep")
def grep(req: GrepRequest):
    target = safe_path(req.path) if req.path else WORKSPACE_DIR

    flags = re.DOTALL | re.MULTILINE if req.multiline else 0
    if req.case_insensitive:
        flags |= re.IGNORECASE
    try:
        compiled = re.compile(req.pattern, flags)
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Invalid regex: {e}")

    files = [target] if target.is_file() else _collect_files(target, req.glob)

    # ── files_with_matches ────────────────────────────────────────────────────
    if req.output_mode == "files_with_matches":
        hits: list[str] = []
        for f in files:
            try:
                if compiled.search(f.read_text(errors="replace")):
                    hits.append(str(f.relative_to(WORKSPACE_DIR)))
            except Exception:
                continue
            if req.head_limit and len(hits) >= req.head_limit:
                break
        return {"output": "\n".join(hits)}

    # ── count ─────────────────────────────────────────────────────────────────
    if req.output_mode == "count":
        lines_out: list[str] = []
        for f in files:
            try:
                cnt = len(compiled.findall(f.read_text(errors="replace")))
                if cnt:
                    lines_out.append(f"{f.relative_to(WORKSPACE_DIR)}:{cnt}")
            except Exception:
                continue
        if req.head_limit:
            lines_out = lines_out[:req.head_limit]
        return {"output": "\n".join(lines_out)}

    # ── content ───────────────────────────────────────────────────────────────
    ctx_before = max(req.context, req.context_before)
    ctx_after = max(req.context, req.context_after)
    output_lines: list[str] = []

    for f in files:
        try:
            file_lines = f.read_text(errors="replace").splitlines()
        except Exception:
            continue

        rel = str(f.relative_to(WORKSPACE_DIR))
        # Collect indices of matching lines
        match_indices = [i for i, ln in enumerate(file_lines) if compiled.search(ln)]
        if not match_indices:
            continue

        # Build contiguous groups respecting context
        shown: set[int] = set()
        groups: list[list[int]] = []
        current: list[int] = []
        for mi in match_indices:
            rng = range(max(0, mi - ctx_before), min(len(file_lines), mi + ctx_after + 1))
            for idx in rng:
                if idx not in shown:
                    if current and idx > current[-1] + 1:
                        groups.append(current)
                        current = []
                    current.append(idx)
                    shown.add(idx)
        if current:
            groups.append(current)

        first_group = True
        for group in groups:
            if not first_group:
                output_lines.append("--")
            first_group = False
            for idx in group:
                is_match = idx in set(match_indices)
                sep = ":" if is_match else "-"
                if req.line_numbers:
                    output_lines.append(f"{rel}{sep}{idx + 1}{sep}{file_lines[idx]}")
                else:
                    output_lines.append(f"{rel}{sep}{file_lines[idx]}")

        if req.head_limit and len(output_lines) >= req.head_limit:
            output_lines = output_lines[:req.head_limit]
            break

    return {"output": "\n".join(output_lines)}


# ── LS ────────────────────────────────────────────────────────────────────────

@app.get("/list_directory")
def list_directory(path: str = Query("", description="Path relative to /workspace")):
    target = safe_path(path) if path else WORKSPACE_DIR
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Not found: {path}")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail=f"Not a directory: {path}")

    entries = [
        {
            "name": e.name,
            "type": "directory" if e.is_dir() else "file",
            "size": e.stat().st_size if e.is_file() else None,
        }
        for e in sorted(target.iterdir())
    ]
    return {"path": str(target.relative_to(WORKSPACE_DIR)), "entries": entries}


# ── Bash ──────────────────────────────────────────────────────────────────────

@app.post("/bash")
def bash(req: BashRequest):
    logger.info("Bash  command=%r  ts=%s", req.command, datetime.utcnow().isoformat())
    timeout_sec = req.timeout / 1000

    try:
        result = subprocess.run(
            req.command,
            shell=True,
            cwd=str(WORKSPACE_DIR),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        output = result.stdout
        if result.stderr:
            output += result.stderr
        return {"output": output, "exit_code": result.returncode}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail=f"Command timed out after {req.timeout}ms")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── DeleteFile ────────────────────────────────────────────────────────────────

@app.delete("/delete_file")
def delete_file(file_path: str = Query(...)):
    target = safe_path(file_path)
    logger.info("DeleteFile  file_path=%s  ts=%s", file_path, datetime.utcnow().isoformat())
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Not found: {file_path}")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"message": f"Deleted: {file_path}"}


# ── MoveFile ──────────────────────────────────────────────────────────────────

@app.post("/move_file")
def move_file(req: MoveRequest):
    src = safe_path(req.source)
    dst = safe_path(req.destination)
    if not src.exists():
        raise HTTPException(status_code=404, detail=f"Source not found: {req.source}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return {"message": f"Moved {req.source} → {req.destination}"}


# ── Git helpers ───────────────────────────────────────────────────────────────

def _git(args: list[str], timeout: int = 30) -> str:
    """Run a git command in WORKSPACE_DIR; raise HTTPException on failure."""
    try:
        r = subprocess.run(
            ["git"] + args,
            cwd=str(WORKSPACE_DIR),
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

@app.get("/git/status")
def git_status():
    """Working tree status — staged, unstaged, and untracked files."""
    raw = _git(["status", "--porcelain=v1", "--untracked-files=all"])
    files = []
    for line in raw.splitlines():
        if not line:
            continue
        xy, path = line[:2], line[3:]
        # Handle renames: "old -> new"
        if " -> " in path:
            old, new = path.split(" -> ", 1)
            files.append({"xy": xy, "path": new, "orig_path": old})
        else:
            files.append({"xy": xy, "path": path})
    return {"files": files, "summary": _git(["status", "--short"])}


# ── Git diff ──────────────────────────────────────────────────────────────────

@app.get("/git/diff", response_class=PlainTextResponse)
def git_diff(
    path: str = Query("", description="Restrict diff to this file/directory"),
    ref: str = Query("", description="Ref to diff against, e.g. HEAD, main, <hash>"),
    staged: bool = Query(False, description="Show staged (indexed) changes"),
    stat: bool = Query(False, description="Show diffstat summary instead of full patch"),
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
    return _git(args)


# ── Git diff for a specific commit ───────────────────────────────────────────

@app.get("/git/diff/{commit_hash}", response_class=PlainTextResponse)
def git_diff_commit(commit_hash: str):
    """Unified diff introduced by a specific commit (commit vs its parent)."""
    return _git(["diff", f"{commit_hash}^", commit_hash])


# ── Git log ───────────────────────────────────────────────────────────────────

@app.get("/git/log")
def git_log(
    max_count: int = Query(20, ge=1, le=500),
    path: str = Query("", description="Only commits touching this path"),
    ref: str = Query("HEAD", description="Branch, tag, or commit to start from"),
    oneline: bool = Query(False, description="Compact one-line format"),
):
    """Commit history with hash, author, date, and message."""
    if oneline:
        fmt = "--oneline"
    else:
        fmt = "--pretty=format:%H%x09%an%x09%ai%x09%s"

    args = ["log", fmt, f"--max-count={max_count}", ref]
    if path:
        args += ["--", path]

    raw = _git(args)
    if oneline:
        return {"log": raw}

    commits = []
    for line in raw.splitlines():
        parts = line.split("\t", 3)
        if len(parts) == 4:
            commits.append({"hash": parts[0], "author": parts[1], "date": parts[2], "subject": parts[3]})
    return {"commits": commits}


# ── Git tree (ls-tree + untracked) ───────────────────────────────────────────

@app.get("/git/tree")
def git_tree(
    path: str = Query("", description="Subdirectory to list"),
    ref: str = Query("HEAD", description="Commit, branch, or tag"),
    recursive: bool = Query(True, description="Recurse into subdirectories"),
):
    """List all files tracked by git at the given ref, plus untracked and staged-new files."""
    prefix = path.lstrip("/")

    # Committed files via ls-tree (may fail on empty repos)
    tracked: set[str] = set()
    try:
        args = ["ls-tree", "--name-only"]
        if recursive:
            args.append("-r")
        args.append(ref)
        if prefix:
            args.append(prefix)
        raw = _git(args)
        tracked = {f for f in raw.splitlines() if f}
    except HTTPException:
        pass  # empty repo or bad ref — fall through to status-based listing

    # Untracked (??) and staged-new (A) files via git status
    # --untracked-files=all expands untracked directories to individual files
    extra: set[str] = set()
    try:
        status_raw = _git(["status", "--porcelain=v1", "--untracked-files=all"])
        for line in status_raw.splitlines():
            if not line:
                continue
            xy = line[:2]
            file_path = line[3:].split(" -> ")[-1]  # handle renames
            # ?? = untracked,  A  = staged new file not yet committed
            if xy in ("??", "A ") and file_path not in tracked:
                if not prefix or file_path.startswith(prefix):
                    extra.add(file_path)
    except HTTPException:
        pass

    files = sorted(tracked | extra)
    return {"files": files, "ref": ref}


# ── Git show (file at ref) ────────────────────────────────────────────────────

@app.get("/git/show", response_class=PlainTextResponse)
def git_show(
    path: str = Query(..., description="File path relative to /workspace"),
    ref: str = Query("HEAD", description="Commit, branch, or tag"),
    line_numbers: bool = Query(True, description="Prefix lines with line numbers"),
):
    """Return the content of a file as it exists at a given git ref."""
    content = _git(["show", f"{ref}:{path.lstrip('/')}"])
    if not line_numbers:
        return content
    lines = content.splitlines(keepends=True)
    return _cat_n(lines, 1)


# ── Git blame ────────────────────────────────────────────────────────────────

@app.get("/git/blame", response_class=PlainTextResponse)
def git_blame(
    path: str = Query(..., description="File path relative to /workspace"),
    ref: str = Query("HEAD"),
):
    """Show which commit and author last modified each line of a file."""
    return _git(["blame", ref, "--", path.lstrip("/")])


# ── Git branches ─────────────────────────────────────────────────────────────

@app.get("/git/branches")
def git_branches(all: bool = Query(False, description="Include remote-tracking branches")):
    """List local (and optionally remote) branches with their latest commit."""
    args = ["branch", "-v"]
    if all:
        args.append("-a")
    raw = _git(args)
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


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=API_PORT, reload=False)
