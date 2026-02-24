from fastapi import APIRouter, HTTPException

from ..models import CreateSessionRequest
from ..session_manager import create_session, delete_session, get_session, list_sessions

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("")
def session_create(req: CreateSessionRequest):
    try:
        return create_session(req.branch, req.user_name, req.create_branch, req.start_point)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("")
def session_list():
    return {"sessions": list_sessions()}


@router.get("/{session_id}")
def session_get(session_id: str):
    s = get_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    return s


@router.delete("/{session_id}")
def session_delete(session_id: str):
    if not delete_session(session_id):
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    return {"message": f"Session {session_id} deleted"}
