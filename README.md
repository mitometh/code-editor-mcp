# Remote File Editing Toolkit — POC

Let a Claude MCP agent read and edit files living inside a Docker container.

```
Claude (MCP client)
    │  tool calls
    ▼
mcp/server.py       ← runs on your local machine (Python)
    │  HTTP requests
    ▼
server/app/         ← FastAPI app, runs inside Docker container
    │  file I/O + git
    ▼
/workspace/         ← GitHub repo cloned here at startup
```

---

## Prerequisites

| Tool | Min version |
|------|-------------|
| Docker + Docker Compose | v2 |
| Python | 3.10+ |
| pip | any recent |

---

## Quick Start

### 1. Start the Docker container

```bash
# Clone this repo / copy files, then:
cd docker-poc

# Copy and edit environment variables
cp .env.example .env

# Set your target GitHub repo (must be public)
export GITHUB_REPO_URL="https://github.com/your-user/your-repo.git"

docker compose up --build
```

The container will:
1. Clone `GITHUB_REPO_URL` into `/workspace`
2. Start the FastAPI file server on port **8000**

Verify it's running:

```bash
curl http://localhost:8000/file/list_directory
```

---

### 2. Install MCP server dependencies (local machine)

```bash
pip install -r mcp/requirements.txt
```

---

### 3. Register the MCP server with Claude

Add this to your Claude MCP config (e.g. `~/.claude/claude_desktop_config.json` or `.mcp.json` in the project):

```json
{
  "mcpServers": {
    "remote-file-editor": {
      "command": "python",
      "args": ["/absolute/path/to/docker-poc/mcp/server.py"],
      "env": {
        "CONTAINER_BASE_URL": "http://localhost:8000"
      }
    }
  }
}
```

Restart Claude Desktop. You should see the `remote-file-editor` server listed.

---

## Available Tools

### File tools

| Tool | What it does |
|------|--------------|
| `Read(file_path, offset?, limit?)` | Return file content with line numbers |
| `Write(file_path, content)` | Create or overwrite a file |
| `Edit(file_path, old_string, new_string, replace_all?)` | Targeted string replacement |
| `Glob(pattern, path?)` | Find files matching a glob pattern |
| `Grep(pattern, path?, glob?, output_mode?, ...)` | Search file contents with regex |
| `Bash(command, timeout?)` | Execute a shell command in /workspace |
| `LS(path?)` | List files and subdirectories |
| `DeleteFile(file_path)` | Delete file or directory (recursive) |
| `MoveFile(source, destination)` | Move or rename |
| `GetProjectContext()` | Load CLAUDE.md, settings, and MCP config into context |

### Git tools

| Tool | What it does |
|------|--------------|
| `GitStatus()` | Show working tree status |
| `GitDiff(path?, ref?, staged?, stat?)` | Show unified diff |
| `GitLog(max_count?, path?, ref?, oneline?)` | Show commit history |
| `GitTree(path?, ref?)` | List all tracked files at a ref |
| `GitShow(path, ref?)` | Read a file at a specific git ref |
| `GitBlame(path, ref?)` | Show per-line commit and author |
| `GitBranches(all?)` | List branches |
| `GitAdd(paths?)` | Stage files |
| `GitCommit(message, author?)` | Create a commit |
| `GitCheckout(ref, create?)` | Switch branch or commit |
| `GitPush(remote?, branch?, force?)` | Push commits to remote |
| `GitPull(remote?, branch?)` | Pull and merge from remote |
| `GitFetch(remote?, prune?)` | Fetch without merging |
| `GitStash(action?, message?)` | Save or restore stashed changes |
| `GitReset(ref?, mode?, paths?)` | Reset HEAD or unstage files |

All file paths are relative to `/workspace` and sandboxed — path traversal is blocked.

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GITHUB_REPO_URL` | _(empty)_ | Repo to clone on container startup |
| `WORKSPACE_DIR` | `/workspace` | Root for all file operations |
| `API_PORT` | `8000` | Port the FastAPI server listens on |
| `CONTAINER_BASE_URL` | `http://localhost:8000` | MCP server → container URL |

---

## Safety Notes

- All paths are resolved and checked to stay inside `WORKSPACE_DIR`.
- `Edit` requires `old_string` to match **exactly once** unless `replace_all=true`.
- `Write` and `DeleteFile` are logged with a UTC timestamp.

---

## File Layout

```
docker-poc/
├── .env.example
├── .gitignore
├── .mcp.json
├── docker-compose.yml
├── docs/
│   └── requirements.md
├── mcp/
│   ├── http_client.py       # HTTP transport helpers
│   ├── requirements.txt     # local Python deps
│   └── server.py            # MCP server (runs on local machine)
└── server/
    ├── Dockerfile
    ├── entrypoint.sh        # clones repo, then starts uvicorn
    ├── requirements.txt
    ├── static/
    │   └── viewer.html      # browser-based file viewer
    └── app/
        ├── config.py
        ├── main.py          # FastAPI app entry point
        ├── models.py
        ├── utils.py
        └── routes/
            ├── files.py     # /file/* endpoints
            └── git.py       # /git/* endpoints
```

---

## Development Tips

Run the FastAPI server locally (without Docker) for faster iteration:

```bash
pip install -r server/requirements.txt
WORKSPACE_DIR=/tmp/workspace uvicorn server.app.main:app --reload
```

Run the MCP server in dev mode:

```bash
CONTAINER_BASE_URL=http://localhost:8000 mcp dev mcp/server.py
```
