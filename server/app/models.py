from pydantic import BaseModel


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
    context: int = 0
    context_before: int = 0
    context_after: int = 0
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


class GitAddRequest(BaseModel):
    paths: list[str] = ["."]


class GitCommitRequest(BaseModel):
    message: str
    author: str = ""


class GitCheckoutRequest(BaseModel):
    ref: str
    create: bool = False


class GitPushRequest(BaseModel):
    remote: str = "origin"
    branch: str = ""
    force: bool = False


class GitPullRequest(BaseModel):
    remote: str = "origin"
    branch: str = ""


class GitFetchRequest(BaseModel):
    remote: str = "origin"
    prune: bool = False


class GitStashRequest(BaseModel):
    action: str = "push"
    message: str = ""


class GitResetRequest(BaseModel):
    ref: str = "HEAD"
    mode: str = "mixed"
    paths: list[str] = []
