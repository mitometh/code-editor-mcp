#!/bin/sh
set -e

WORKSPACE_DIR="${WORKSPACE_DIR:-/workspace}"
API_PORT="${API_PORT:-8000}"

echo "==> Creating workspace at ${WORKSPACE_DIR}"
mkdir -p "${WORKSPACE_DIR}"

if [ -n "${GITHUB_REPO_URL}" ]; then
    echo "==> Cloning ${GITHUB_REPO_URL} into ${WORKSPACE_DIR}"
    git clone "${GITHUB_REPO_URL}" "${WORKSPACE_DIR}"
else
    echo "==> GITHUB_REPO_URL not set — starting with empty workspace"
fi

echo "==> Starting FastAPI server on port ${API_PORT}"
exec uvicorn api:app \
    --host 0.0.0.0 \
    --port "${API_PORT}" \
    --workers 1
