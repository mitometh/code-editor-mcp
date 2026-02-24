# Remote File Editing Toolkit — POC

Let a Claude MCP agent read and edit files living inside a Docker container.

```
Claude (MCP client)
    │  tool calls
    ▼
mcp_server.py       ← runs on your local machine (Python)
    │  HTTP requests
    ▼
FastAPI (api.py)    ← runs inside Docker container
    │  file I/O
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

# Set your target GitHub repo (must be public)
export GITHUB_REPO_URL="https://github.com/your-user/your-repo.git"

docker compose up --build
```

The container will:
1. Clone `GITHUB_REPO_URL` into `/workspace`
2. Start the FastAPI file server on port **8000**

Verify it's running:

```bash
curl http://localhost:8000/list_directory
```

---

### 2. Install MCP server dependencies (local machine)

```bash
pip install -r requirements-mcp.txt
```

---

### 3. Register the MCP server with Claude

Add this to your Claude MCP config (e.g. `~/.claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "remote-file-editor": {
      "command": "python",
      "args": ["/absolute/path/to/docker-poc/mcp_server.py"],
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

| Tool | What it does |
|------|--------------|
| `read_file(path)` | Return file content |
| `list_directory(path)` | List files & subdirs |
| `write_file(path, content)` | Create or overwrite a file |
| `create_directory(path)` | Create directory (+ parents) |
| `str_replace(path, old_str, new_str)` | Targeted single-occurrence replace |
| `insert_lines(path, insert_after_line, text)` | Insert text after line N |
| `delete_file(path)` | Delete file or directory |
| `move_file(source, destination)` | Move or rename |

All paths are relative to `/workspace` and sandboxed — path traversal is blocked.

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
- `str_replace` requires the target string to match **exactly once**.
- `write_file` and `delete_file` are logged with a UTC timestamp.

---

## File Layout

```
docker-poc/
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh        # clones repo, then starts uvicorn
├── api.py               # FastAPI file server (runs in container)
├── mcp_server.py        # MCP server (runs on local machine)
├── requirements-mcp.txt # local Python deps
└── README.md
```

---

## Development Tips

Run the FastAPI server locally (without Docker) for faster iteration:

```bash
pip install fastapi "uvicorn[standard]"
WORKSPACE_DIR=/tmp/workspace python api.py
```

Run the MCP server in dev mode:

```bash
CONTAINER_BASE_URL=http://localhost:8000 mcp dev mcp_server.py
```
