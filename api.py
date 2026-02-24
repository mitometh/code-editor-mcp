import os
import re
import shutil
import subprocess
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

WORKSPACE_DIR = Path(os.environ.get("WORKSPACE_DIR", "/workspace")).resolve()
API_PORT = int(os.environ.get("API_PORT", "8000"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Remote File Server")


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


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=API_PORT, reload=False)
