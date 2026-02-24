import re
import shutil
import subprocess
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse

from ..config import WORKSPACE_DIR
from ..models import BashRequest, EditRequest, GrepRequest, MoveRequest, WriteRequest
from ..session_manager import get_session as _get_session
from ..utils import _cat_n, _collect_files, safe_path

router = APIRouter(prefix="/file")
logger = logging.getLogger(__name__)


def _workspace(session_id: Optional[str] = Query(None)) -> Path:
    """Resolve the active workspace: session worktree or default WORKSPACE_DIR."""
    if session_id:
        s = _get_session(session_id)
        if not s:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
        return Path(s["worktree_path"])
    return WORKSPACE_DIR


# ── Read ──────────────────────────────────────────────────────────────────────

@router.get("/read_file", response_class=PlainTextResponse)
def read_file(
    file_path: str = Query(...),
    offset: int = Query(1, ge=1, description="1-based line to start reading from"),
    limit: int = Query(0, ge=0, description="Max lines to read (0 = all remaining)"),
    workspace: Path = Depends(_workspace),
):
    target = safe_path(file_path, workspace)
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

@router.post("/write_file")
def write_file(
    req: WriteRequest,
    workspace: Path = Depends(_workspace),
):
    target = safe_path(req.file_path, workspace)
    logger.info("Write  file_path=%s  ts=%s", req.file_path, datetime.utcnow().isoformat())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(req.content)
    return {"message": f"File written: {req.file_path}"}


# ── Edit ──────────────────────────────────────────────────────────────────────

@router.post("/edit")
def edit(
    req: EditRequest,
    workspace: Path = Depends(_workspace),
):
    target = safe_path(req.file_path, workspace)
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

@router.get("/glob")
def glob_files(
    pattern: str = Query(..., description="Glob pattern e.g. **/*.py"),
    path: str = Query("", description="Base directory relative to workspace root"),
    workspace: Path = Depends(_workspace),
):
    base = safe_path(path, workspace) if path else workspace
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

    return {"matches": [str(p.relative_to(workspace)) for p in matches]}


# ── Grep ──────────────────────────────────────────────────────────────────────

@router.post("/grep")
def grep(
    req: GrepRequest,
    workspace: Path = Depends(_workspace),
):
    target = safe_path(req.path, workspace) if req.path else workspace

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
                    hits.append(str(f.relative_to(workspace)))
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
                    lines_out.append(f"{f.relative_to(workspace)}:{cnt}")
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

        rel = str(f.relative_to(workspace))
        match_indices = [i for i, ln in enumerate(file_lines) if compiled.search(ln)]
        if not match_indices:
            continue

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

@router.get("/list_directory")
def list_directory(
    path: str = Query("", description="Path relative to workspace root"),
    workspace: Path = Depends(_workspace),
):
    target = safe_path(path, workspace) if path else workspace
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
    return {"path": str(target.relative_to(workspace)), "entries": entries}


# ── Bash ──────────────────────────────────────────────────────────────────────

@router.post("/bash")
def bash(
    req: BashRequest,
    workspace: Path = Depends(_workspace),
):
    logger.info("Bash  command=%r  ts=%s", req.command, datetime.utcnow().isoformat())
    timeout_sec = req.timeout / 1000

    try:
        result = subprocess.run(
            req.command,
            shell=True,
            cwd=str(workspace),
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

@router.delete("/delete_file")
def delete_file(
    file_path: str = Query(...),
    workspace: Path = Depends(_workspace),
):
    target = safe_path(file_path, workspace)
    logger.info("DeleteFile  file_path=%s  ts=%s", file_path, datetime.utcnow().isoformat())
    if not target.exists():
        raise HTTPException(status_code=404, detail=f"Not found: {file_path}")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"message": f"Deleted: {file_path}"}


# ── MoveFile ──────────────────────────────────────────────────────────────────

@router.post("/move_file")
def move_file(
    req: MoveRequest,
    workspace: Path = Depends(_workspace),
):
    src = safe_path(req.source, workspace)
    dst = safe_path(req.destination, workspace)
    if not src.exists():
        raise HTTPException(status_code=404, detail=f"Source not found: {req.source}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return {"message": f"Moved {req.source} → {req.destination}"}
