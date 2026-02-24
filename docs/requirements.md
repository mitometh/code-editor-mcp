# Remote File Editing Toolkit — POC Requirements

## Overview

Build a toolkit that lets an LLM coding agent (Claude) read and edit files on a remote environment, simulated in this POC using Docker. The repo being edited lives on GitHub and is cloned into the container at startup.

## Architecture

```
Claude (MCP client)
    │  tool calls
    ▼
MCP Server          ← runs on your local machine (Python)
    │  HTTP requests
    ▼
FastAPI Server      ← runs inside Docker container
    │  file I/O
    ▼
/workspace/         ← GitHub repo cloned here at container startup
```

## Components to Build

### 1. Docker Container

- Base image: Python (or any Linux base)
- On startup: `git clone <GITHUB_REPO_URL>` into `/workspace`
- Runs FastAPI server on port 8000
- Exposes port 8000 to the host machine
- Configured via environment variables (`GITHUB_REPO_URL`)

### 2. FastAPI File Server (inside container)

Exposes the following HTTP endpoints:

| Method   | Endpoint              | Description                             |
| -------- | --------------------- | --------------------------------------- |
| `GET`    | `/read_file?path=`    | Return file content as text             |
| `GET`    | `/list_directory?path=` | Return directory listing              |
| `POST`   | `/write_file`         | Create or fully overwrite a file        |
| `POST`   | `/create_directory`   | Create a new directory                  |
| `POST`   | `/str_replace`        | Replace a unique string in a file       |
| `POST`   | `/insert_lines`       | Insert text after a given line number   |
| `DELETE`  | `/delete_file?path=`  | Delete a file                           |
| `POST`   | `/move_file`          | Move or rename a file or directory      |

All paths are sandboxed to `/workspace` — requests outside this root are rejected.

### 3. MCP Server (on your local machine)

- Wraps each FastAPI endpoint as a Claude-callable MCP tool
- Handles HTTP communication with the container
- Returns clear success/error messages the agent can act on
- Tool descriptions written to be LLM-friendly

## Safety Rules

- All file paths must resolve within `/workspace` — path traversal (e.g. `../../etc`) is blocked
- `str_replace` fails if the target string matches zero or more than one location
- Destructive operations (`delete_file`, `write_file`) log every call with timestamp and path

## Configuration

Everything driven by environment variables, no hardcoded values:

| Variable          | Description                                          |
| ----------------- | ---------------------------------------------------- |
| `GITHUB_REPO_URL` | The repo to clone on container startup               |
| `WORKSPACE_DIR`   | Root directory for file operations (default: `/workspace`) |
| `API_PORT`        | Port the FastAPI server listens on (default: `8000`) |

## Out of Scope for POC

- Authentication / API keys on the HTTP server
- Private GitHub repo support (public repo only)
- Remote code execution
- Multi-agent / concurrent access

## Success Criteria

The POC is complete when Claude can:

1. List files in the cloned GitHub repo
2. Read a file's content
3. Make a targeted edit using `str_replace`
4. Write a new file
5. Delete or move a file

All via MCP tool calls, with the file changes visible inside the running Docker container.

## Deliverables

- `server/Dockerfile` + `docker-compose.yml`
- `server/app/` — FastAPI file server (files and git routes)
- `mcp/server.py` — MCP server with tool definitions
- `mcp/http_client.py` — HTTP transport layer
- `README.md` — setup and run instructions
