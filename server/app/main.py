import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .routes.files import router as files_router
from .routes.git import router as git_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

app = FastAPI(title="Remote File Server")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(files_router)
app.include_router(git_router)


@app.get("/")
def viewer():
    return FileResponse(Path(__file__).parent.parent / "static" / "viewer.html", media_type="text/html")
