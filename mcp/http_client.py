"""
HTTP transport layer for the MCP server.
All communication with the container's FastAPI server goes through these helpers.
"""
import json
import os

import httpx

BASE_URL = os.environ.get("CONTAINER_BASE_URL", "http://localhost:8000").rstrip("/")


def _get(endpoint: str, **params) -> str:
    """GET request; returns response text. Returns an error string on failure."""
    try:
        r = httpx.get(
            f"{BASE_URL}{endpoint}",
            params={k: v for k, v in params.items() if v is not None},
            timeout=60,
        )
        r.raise_for_status()
        return r.text
    except httpx.HTTPStatusError as e:
        return f"ERROR {e.response.status_code}: {e.response.text}"
    except Exception as e:
        return f"ERROR: {e}"


def _get_json(endpoint: str, **params) -> dict:
    """GET request; returns parsed JSON. Raises httpx exceptions on failure."""
    r = httpx.get(
        f"{BASE_URL}{endpoint}",
        params={k: v for k, v in params.items() if v is not None},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def _post(endpoint: str, payload: dict) -> str:
    """POST JSON; returns the response 'output' or 'message' as a string."""
    try:
        r = httpx.post(f"{BASE_URL}{endpoint}", json=payload, timeout=120)
        r.raise_for_status()
        data = r.json()
        return data.get("output") or data.get("message") or json.dumps(data)
    except httpx.HTTPStatusError as e:
        return f"ERROR {e.response.status_code}: {e.response.text}"
    except Exception as e:
        return f"ERROR: {e}"


def _delete(endpoint: str, **params) -> str:
    """DELETE request; returns the response 'message' as a string."""
    try:
        r = httpx.delete(f"{BASE_URL}{endpoint}", params=params, timeout=30)
        r.raise_for_status()
        return r.json().get("message", "Done")
    except httpx.HTTPStatusError as e:
        return f"ERROR {e.response.status_code}: {e.response.text}"
    except Exception as e:
        return f"ERROR: {e}"
