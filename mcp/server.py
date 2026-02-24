"""
MCP Server — remote workspace tools that mirror Claude Code's built-in tools.

Tools:    Read, Write, Edit, Glob, Grep, Bash, LS, DeleteFile, MoveFile,
          GetProjectContext
Resources: workspace://<path>  — any file in the remote workspace
           workspace://CLAUDE.md — project instructions (auto-browsable)

Run with:
    python mcp/server.py

Register in .mcp.json (stdio transport):
    {
      "command": "python3.13",
      "args": ["/path/to/mcp/server.py"],
      "env": { "CONTAINER_BASE_URL": "http://localhost:8000" }
    }
"""

from typing import Optional

import httpx
from mcp.server.fastmcp import FastMCP

from http_client import BASE_URL, _get, _post, _delete

mcp = FastMCP("remote-file-editor")


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def Read(
    file_path: str,
    offset: Optional[int] = None,
    limit: Optional[int] = None,
) -> str:
    """
    Read a file from the remote workspace.
    Returns content with line numbers in the format used by Claude Code (     1→...).
    By default reads the whole file; use offset/limit to read a slice.

    Args:
        file_path: Path relative to /workspace (e.g. "src/main.py").
        offset: 1-based line number to start reading from.
        limit: Maximum number of lines to return.
    """
    return _get("/file/read_file", file_path=file_path, offset=offset, limit=limit)


@mcp.tool()
def Write(file_path: str, content: str) -> str:
    """
    Create or fully overwrite a file in the remote workspace.
    Missing parent directories are created automatically.

    Args:
        file_path: Path relative to /workspace.
        content: The complete new content of the file.
    """
    return _post("/file/write_file", {"file_path": file_path, "content": content})


@mcp.tool()
def Edit(
    file_path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> str:
    """
    Perform an exact string replacement in a file in the remote workspace.
    By default requires old_string to appear exactly once (safe targeted edit).
    Set replace_all=true to replace every occurrence.

    Args:
        file_path: Path relative to /workspace.
        old_string: The exact string to find. Must match exactly once unless replace_all=true.
        new_string: The replacement string.
        replace_all: If true, replace every occurrence instead of requiring uniqueness.
    """
    return _post("/file/edit", {
        "file_path": file_path,
        "old_string": old_string,
        "new_string": new_string,
        "replace_all": replace_all,
    })


@mcp.tool()
def Glob(pattern: str, path: str = "") -> str:
    """
    Find files in the remote workspace matching a glob pattern.
    Results are sorted by modification time (most recent first).

    Args:
        pattern: Glob pattern relative to the search root, e.g. "**/*.py" or "src/**/*.ts".
        path: Subdirectory to search in (relative to /workspace). Defaults to workspace root.
    """
    try:
        data = httpx.get(f"{BASE_URL}/file/glob", params={"pattern": pattern, "path": path}, timeout=30)
        data.raise_for_status()
        matches = data.json().get("matches", [])
        return "\n".join(matches) if matches else "(no matches)"
    except httpx.HTTPStatusError as e:
        return f"ERROR {e.response.status_code}: {e.response.text}"
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def Grep(
    pattern: str,
    path: str = "",
    glob: str = "",
    output_mode: str = "files_with_matches",
    context: int = 0,
    case_insensitive: bool = False,
    line_numbers: bool = True,
    head_limit: int = 0,
    multiline: bool = False,
) -> str:
    """
    Search file contents in the remote workspace using a regular expression.

    Args:
        pattern: Regular expression to search for.
        path: File or directory to search (relative to /workspace). Defaults to workspace root.
        glob: Glob pattern to filter which files are searched, e.g. "*.py".
        output_mode: One of:
            "files_with_matches" (default) — list of matching file paths,
            "content"  — matching lines with optional context,
            "count"    — match count per file.
        context: Lines of context to show before and after each match (content mode).
        case_insensitive: Case-insensitive matching.
        line_numbers: Prefix matching lines with line numbers (content mode).
        head_limit: Return only the first N results.
        multiline: Allow . to match newlines; patterns can span lines.
    """
    return _post("/file/grep", {
        "pattern": pattern,
        "path": path,
        "glob": glob,
        "output_mode": output_mode,
        "context": context,
        "case_insensitive": case_insensitive,
        "line_numbers": line_numbers,
        "head_limit": head_limit,
        "multiline": multiline,
    })


@mcp.tool()
def Bash(command: str, timeout: int = 120000) -> str:
    """
    Execute a shell command inside the remote workspace container.
    Always runs from /workspace — all file access is scoped to that directory.
    stdout and stderr are returned together.

    Args:
        command: Shell command to run (executed via /bin/sh -c).
        timeout: Timeout in milliseconds (default 120 000 = 2 minutes).
    """
    return _post("/file/bash", {"command": command, "timeout": timeout})


@mcp.tool()
def LS(path: str = "") -> str:
    """
    List files and subdirectories in a directory of the remote workspace.

    Args:
        path: Directory path relative to /workspace. Defaults to workspace root.
    """
    try:
        r = httpx.get(f"{BASE_URL}/file/list_directory", params={"path": path}, timeout=30)
        r.raise_for_status()
        data = r.json()
        lines = [f"/{data['path']}:"]
        for e in data["entries"]:
            tag = "[DIR]" if e["type"] == "directory" else "     "
            size = f"  ({e['size']} B)" if e["size"] is not None else ""
            lines.append(f"  {tag}  {e['name']}{size}")
        return "\n".join(lines)
    except httpx.HTTPStatusError as e:
        return f"ERROR {e.response.status_code}: {e.response.text}"
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def DeleteFile(file_path: str) -> str:
    """
    Delete a file or directory (recursively) from the remote workspace.

    Args:
        file_path: Path relative to /workspace.
    """
    return _delete("/file/delete_file", file_path=file_path)


@mcp.tool()
def MoveFile(source: str, destination: str) -> str:
    """
    Move or rename a file or directory within the remote workspace.

    Args:
        source: Current path relative to /workspace.
        destination: Target path relative to /workspace.
    """
    return _post("/file/move_file", {"source": source, "destination": destination})


# ── Git tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def GitStatus() -> str:
    """
    Show the working tree status of the remote workspace repository.
    Returns staged, unstaged, and untracked files with their XY status codes.

    Status codes: M=modified  A=added  D=deleted  R=renamed  ?=untracked  !=ignored
    """
    try:
        r = httpx.get(f"{BASE_URL}/git/status", timeout=15)
        r.raise_for_status()
        data = r.json()
        if not data["files"]:
            return "Nothing to commit, working tree clean."
        lines = []
        for f in data["files"]:
            orig = f"  (was {f['orig_path']})" if "orig_path" in f else ""
            lines.append(f"  {f['xy']}  {f['path']}{orig}")
        return "\n".join(lines)
    except httpx.HTTPStatusError as e:
        return f"ERROR {e.response.status_code}: {e.response.text}"
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def GitDiff(
    path: str = "",
    ref: str = "",
    staged: bool = False,
    stat: bool = False,
) -> str:
    """
    Show a unified diff of changes in the remote workspace.

    Args:
        path: Restrict the diff to a specific file or directory.
        ref: Compare against this ref (branch, tag, or commit hash).
             Examples: "HEAD", "main", "abc1234", "HEAD~3"
        staged: Show staged (indexed) changes instead of unstaged.
        stat: Show a diffstat summary (changed files + line counts) instead of the full patch.
    """
    return _get("/git/diff", path=path, ref=ref, staged=staged, stat=stat)


@mcp.tool()
def GitLog(
    max_count: int = 20,
    path: str = "",
    ref: str = "HEAD",
    oneline: bool = False,
) -> str:
    """
    Show the commit history of the remote workspace repository.

    Args:
        max_count: Maximum number of commits to return (default 20, max 500).
        path: Only show commits that touched this file or directory.
        ref: Start from this branch, tag, or commit (default HEAD).
        oneline: Compact one-line format showing hash + subject only.
    """
    try:
        r = httpx.get(
            f"{BASE_URL}/git/log",
            params={"max_count": max_count, "path": path, "ref": ref, "oneline": oneline},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        if "log" in data:
            return data["log"] or "(no commits)"
        commits = data.get("commits", [])
        if not commits:
            return "(no commits)"
        lines = []
        for c in commits:
            lines.append(f"{c['hash'][:8]}  {c['date'][:10]}  {c['author']}")
            lines.append(f"          {c['subject']}")
        return "\n".join(lines)
    except httpx.HTTPStatusError as e:
        return f"ERROR {e.response.status_code}: {e.response.text}"
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def GitTree(path: str = "", ref: str = "HEAD") -> str:
    """
    List all files tracked by git at a given ref — a full git-aware file tree.
    Unlike LS, this shows every tracked file recursively (ignoring untracked files).

    Args:
        path: Subdirectory to restrict the listing to.
        ref: Commit, branch, or tag to read the tree from (default HEAD).
    """
    try:
        r = httpx.get(
            f"{BASE_URL}/git/tree",
            params={"path": path, "ref": ref, "recursive": True},
            timeout=15,
        )
        r.raise_for_status()
        files = r.json().get("files", [])
        return "\n".join(files) if files else "(empty tree)"
    except httpx.HTTPStatusError as e:
        return f"ERROR {e.response.status_code}: {e.response.text}"
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def GitShow(path: str, ref: str = "HEAD") -> str:
    """
    Read the content of a file as it existed at a specific git ref.
    Returns the file with line numbers (same format as Read).
    Useful for comparing the current version against a past commit.

    Args:
        path: File path relative to /workspace.
        ref: Commit hash, branch, or tag (default HEAD).
             Examples: "HEAD", "main", "HEAD~1", "abc1234"
    """
    return _get("/git/show", path=path, ref=ref, line_numbers=True)


@mcp.tool()
def GitBlame(path: str, ref: str = "HEAD") -> str:
    """
    Show which commit and author last modified each line of a file.

    Args:
        path: File path relative to /workspace.
        ref: Commit, branch, or tag to blame from (default HEAD).
    """
    return _get("/git/blame", path=path, ref=ref)


@mcp.tool()
def GitBranches(all: bool = False) -> str:
    """
    List branches in the remote workspace repository.

    Args:
        all: Include remote-tracking branches (e.g. origin/main) as well.
    """
    try:
        r = httpx.get(f"{BASE_URL}/git/branches", params={"all": all}, timeout=10)
        r.raise_for_status()
        branches = r.json().get("branches", [])
        lines = []
        for b in branches:
            marker = "* " if b["current"] else "  "
            lines.append(f"{marker}{b['name']:30} {b['hash'][:8]}  {b['subject']}")
        return "\n".join(lines) if lines else "(no branches)"
    except httpx.HTTPStatusError as e:
        return f"ERROR {e.response.status_code}: {e.response.text}"
    except Exception as e:
        return f"ERROR: {e}"


@mcp.tool()
def GitAdd(paths: list[str] = None) -> str:
    """
    Stage files for the next commit.

    Args:
        paths: List of file paths to stage (relative to /workspace). Defaults to ["."] (everything).
    """
    return _post("/git/add", {"paths": paths or ["."]})


@mcp.tool()
def GitCommit(message: str, author: str = "") -> str:
    """
    Create a commit with the currently staged changes.

    Args:
        message: Commit message.
        author: Optional author override in "Name <email>" format.
    """
    return _post("/git/commit", {"message": message, "author": author})


@mcp.tool()
def GitCheckout(ref: str, create: bool = False) -> str:
    """
    Switch to a branch or commit, optionally creating a new branch.

    Args:
        ref: Branch name, tag, or commit hash to check out.
        create: If true, create the branch (-b flag).
    """
    return _post("/git/checkout", {"ref": ref, "create": create})


@mcp.tool()
def GitPush(remote: str = "origin", branch: str = "", force: bool = False) -> str:
    """
    Push commits to a remote repository.

    Args:
        remote: Remote name (default "origin").
        branch: Branch to push (default: current branch).
        force: Use --force-with-lease instead of a normal push.
    """
    return _post("/git/push", {"remote": remote, "branch": branch, "force": force})


@mcp.tool()
def GitPull(remote: str = "origin", branch: str = "") -> str:
    """
    Pull and merge changes from a remote repository.

    Args:
        remote: Remote name (default "origin").
        branch: Branch to pull (default: tracking branch).
    """
    return _post("/git/pull", {"remote": remote, "branch": branch})


@mcp.tool()
def GitFetch(remote: str = "origin", prune: bool = False) -> str:
    """
    Fetch changes from a remote without merging.

    Args:
        remote: Remote name (default "origin").
        prune: Remove remote-tracking branches that no longer exist on the remote.
    """
    return _post("/git/fetch", {"remote": remote, "prune": prune})


@mcp.tool()
def GitStash(action: str = "push", message: str = "") -> str:
    """
    Save or restore stashed working tree changes.

    Args:
        action: One of push | pop | list | drop (default "push").
        message: Optional description when action is "push".
    """
    return _post("/git/stash", {"action": action, "message": message})


@mcp.tool()
def GitReset(ref: str = "HEAD", mode: str = "mixed", paths: list[str] = None) -> str:
    """
    Reset HEAD or unstage specific files.

    Args:
        ref: Commit to reset to (default "HEAD").
        mode: soft | mixed | hard — ignored when paths are provided.
        paths: If provided, unstage only these specific files.
    """
    return _post("/git/reset", {"ref": ref, "mode": mode, "paths": paths or []})


# ── Resources (workspace files as MCP resources) ──────────────────────────────

@mcp.resource("workspace://CLAUDE.md")
def claude_md_resource() -> str:
    """Project instructions from CLAUDE.md in the remote workspace."""
    content = _get("/file/read_file", file_path="CLAUDE.md")
    if "ERROR 404" in content:
        return "(No CLAUDE.md found in the remote workspace)"
    return content


@mcp.resource("workspace://{path}")
def workspace_file_resource(path: str) -> str:
    """
    Any file from the remote workspace, readable as a resource.
    URI format: workspace://<path-relative-to-workspace>
    Example:    workspace://src/main.py
    """
    return _get("/file/read_file", file_path=path)


# ── GetProjectContext ──────────────────────────────────────────────────────────

@mcp.tool()
def GetProjectContext() -> str:
    """
    Load all Claude Code config files from the remote workspace into context.
    Call this once at the start of a session before doing any work.

    Fetches (if present):
      - CLAUDE.md                    project-level instructions for the agent
      - .claude/settings.json        project-level Claude Code settings
      - .claude/commands/*.md        custom slash-command definitions
      - .mcp.json                    MCP server config declared by the project
    """
    sections: list[str] = []

    # ── CLAUDE.md ─────────────────────────────────────────────────────────────
    for candidate in ("CLAUDE.md", "claude.md", ".claude/CLAUDE.md"):
        content = _get("/file/read_file", file_path=candidate)
        if not content.startswith("ERROR"):
            sections.append(f"=== {candidate} ===\n{content}")
            break

    # ── .claude/settings.json ─────────────────────────────────────────────────
    content = _get("/file/read_file", file_path=".claude/settings.json")
    if not content.startswith("ERROR"):
        sections.append(f"=== .claude/settings.json ===\n{content}")

    # ── .mcp.json ─────────────────────────────────────────────────────────────
    content = _get("/file/read_file", file_path=".mcp.json")
    if not content.startswith("ERROR"):
        sections.append(f"=== .mcp.json ===\n{content}")

    # ── .claude/commands/*.md  (custom slash commands) ────────────────────────
    try:
        r = httpx.get(f"{BASE_URL}/file/glob", params={"pattern": ".claude/commands/*.md"}, timeout=10)
        if r.status_code == 200:
            for cmd_path in r.json().get("matches", []):
                content = _get("/file/read_file", file_path=cmd_path)
                if not content.startswith("ERROR"):
                    sections.append(f"=== {cmd_path} ===\n{content}")
    except Exception:
        pass

    if not sections:
        return "No Claude Code config files found in the remote workspace."
    return "\n\n".join(sections)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
