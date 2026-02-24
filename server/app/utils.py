from pathlib import Path

from fastapi import HTTPException

from .config import WORKSPACE_DIR


def safe_path(raw: str) -> Path:
    """Resolve path and ensure it stays within WORKSPACE_DIR."""
    resolved = (WORKSPACE_DIR / raw.lstrip("/")).resolve()
    if not str(resolved).startswith(str(WORKSPACE_DIR)):
        raise HTTPException(status_code=400, detail=f"Path '{raw}' escapes workspace root")
    return resolved


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
