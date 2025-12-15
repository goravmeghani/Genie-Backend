# api.py
from __future__ import annotations

import json
import time
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any, List, Optional
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

from fastapi import FastAPI, HTTPException, UploadFile, File, status, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from fastapi.responses import StreamingResponse
from projects import projects_router
from agent import chatbot, retrieve_all_threads
from utils.tools import generate_project_docs_impl
from db_utils import get_user_plan
from billing import router as billing_router
from admin import router as admin_router, pricing_router as public_pricing_router

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage

MAX_PREVIEW_FILES = 25

UPLOAD_STORAGE_ROOT = Path(__file__).resolve().parent / "uploaded_projects"
UPLOAD_STORAGE_ROOT.mkdir(parents=True, exist_ok=True)

# --- Optional: a lightweight titles registry so you can rename threads ---
TITLES_REGISTRY = (UPLOAD_STORAGE_ROOT / ".." / "thread_titles.json").resolve()
TITLES_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
if not TITLES_REGISTRY.exists():
    TITLES_REGISTRY.write_text("{}", encoding="utf-8")


def _load_titles() -> dict[str, str]:
    try:
        return json.loads(TITLES_REGISTRY.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_titles(d: dict[str, str]) -> None:
    TITLES_REGISTRY.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")

# put near your other helpers
import re

def _unique_target_dir_from_filename(filename: str) -> Path:
    """Create a folder under UPLOAD_STORAGE_ROOT based on the zip's base name.
    If it exists, append a numeric suffix: name, name1, name2, ...
    Returns the created directory Path.
    """
    base = Path(filename).stem
    # sanitize: keep alnum, dash, underscore; collapse spaces to underscores
    base = re.sub(r"\s+", "_", base.strip())
    base = re.sub(r"[^A-Za-z0-9_\-]", "", base)
    if not base:
        base = "upload"

    candidate = UPLOAD_STORAGE_ROOT / base
    i = 1
    while candidate.exists():
        candidate = UPLOAD_STORAGE_ROOT / f"{base}{i}"
        i += 1

    candidate.mkdir(parents=True, exist_ok=False)
    return candidate

# ----------------- helpers -----------------
def _to_thread_id(raw: uuid.UUID | str | None) -> str:
    if isinstance(raw, uuid.UUID):
        return str(raw)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return uuid.uuid4().hex


def _serialize_messages(messages: List[BaseMessage]) -> List[dict[str, str]]:
    out: List[dict[str, str]] = []
    for m in messages:
        if isinstance(m, HumanMessage):
            role = "user"
        elif isinstance(m, AIMessage):
            role = "assistant"
        else:
            # hide tool/function messages from API callers
            continue

        content = m.content
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item.get("text", "")))
                else:
                    parts.append(str(item))
            content = "".join(parts)
        else:
            content = str(content)
        out.append({"role": role, "content": content})
    return out


# def _load_messages(thread_id: str) -> List[BaseMessage]:
#     state = chatbot.get_state(config={"configurable": {"thread_id": thread_id}})
#     return state.values.get("messages", [])  # type: ignore[return-value]

def _load_messages(thread_id: str, user_id: str) -> List[BaseMessage]:
    state = chatbot.get_state(
        config={"configurable": {"thread_id": thread_id, "user_id": user_id}}
    )
    return state.values.get("messages", [])



def _first_user_line(messages: List[BaseMessage]) -> Optional[str]:
    for m in messages:
        if isinstance(m, HumanMessage):
            text = str(m.content or "").strip()
            if text:
                return text.splitlines()[0]
    return None


def _pretty_title_from_messages(thread_id: str, messages: List[BaseMessage]) -> str:
    # 1) explicit rename (if present)
    titles = _load_titles()
    if thread_id in titles and titles[thread_id].strip():
        return titles[thread_id]

    # 2) first user line
    first = _first_user_line(messages)
    if first:
        first = first[:50]
        return first + ("…" if len(first) == 50 else "")

    # 3) fallback
    return f"Chat {thread_id[:8]}"


def _create_upload_directory(upload_id: str) -> Path:
    target = UPLOAD_STORAGE_ROOT / upload_id
    target.mkdir(parents=True, exist_ok=False)
    return target


def _safe_extract(zip_file: zipfile.ZipFile, target_dir: Path) -> None:
    for member in zip_file.infolist():
        member_path = Path(member.filename)
        if member_path.is_absolute() or ".." in member_path.parts:
            raise ValueError("Archive contains unsafe paths.")
        zip_file.extract(member, target_dir)


def _summarize_folder(folder: Path) -> tuple[list[str], list[str]]:
    files: list[str] = []
    top_level: list[str] = []
    seen: set[str] = set()
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(folder).as_posix()
        files.append(rel)
        top = rel.split("/", 1)[0]
        if top not in seen:
            seen.add(top)
            top_level.append(top)
    return sorted(files), sorted(top_level)


# ----------------- DTOs -----------------
class MessageDTO(BaseModel):
    role: str
    content: str


class ThreadListItem(BaseModel):
    thread_id: str
    title: str
    message_count: int


class ThreadsResponse(BaseModel):
    threads: List[ThreadListItem] = Field(default_factory=list)


class CreateThreadResponse(BaseModel):
    thread_id: str


class RenameThreadRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    thread_id: str | None = None
    user_id: str = Field(..., min_length=1)


class ChatResponse(BaseModel):
    thread_id: str
    title: str
    reply: str
    messages: List[MessageDTO]


class ThreadMessagesResponse(BaseModel):
    thread_id: str
    title: str
    messages: List[MessageDTO]


class UploadResponse(BaseModel):
    upload_id: str
    file_name: str
    total_files: int
    top_level: List[str] = Field(default_factory=list)
    preview_files: List[str] = Field(default_factory=list)
    folder_path: str


class DocGenerationResponse(BaseModel):
    upload_id: str
    status: str
    readme: str | None = None
    user_manual: str | None = None
    files_analyzed: int = 0
    folder_path: str
    readme_path: str | None = None
    user_manual_path: str | None = None
    fallback_used: bool = False


# ----------------- FastAPI app -----------------
app = FastAPI(title="LangGraph Chatbot API", version="1.1.0")
app.include_router(billing_router)
app.include_router(admin_router)
app.include_router(public_pricing_router)
app.include_router(projects_router) 

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# -------- threads --------
# @app.get("/threads", response_model=ThreadsResponse)
# def list_threads() -> ThreadsResponse:
#     raw_threads = [str(t) for t in retrieve_all_threads()]
#     # derive lightweight metadata
#     items: list[ThreadListItem] = []
#     for tid in raw_threads:
#         msgs = _load_messages(tid, user_id)
#         title = _pretty_title_from_messages(tid, msgs)
#         items.append(
#             ThreadListItem(
#                 thread_id=tid,
#                 title=title,
#                 message_count=len([m for m in msgs if isinstance(m, (HumanMessage, AIMessage))]),
#             )
#         )
#     # newest first (we approximate recency by message_count, ties by id desc)
#     items.sort(key=lambda x: (x.message_count, x.thread_id), reverse=True)
#     return ThreadsResponse(threads=items)
@app.get("/threads", response_model=ThreadsResponse)
def list_threads(user_id: str = Query(...)) -> ThreadsResponse:
    """
    List threads only for this user_id
    """
    raw_threads = [str(t) for t in retrieve_all_threads(user_id)]

    items: list[ThreadListItem] = []
    for tid in raw_threads:
        msgs = _load_messages(tid, user_id)
        title = _pretty_title_from_messages(tid, msgs)
        items.append(
            ThreadListItem(
                thread_id=tid,
                title=title,
                message_count=len(
                    [m for m in msgs if isinstance(m, (HumanMessage, AIMessage))]
                ),
            )
        )

    # keep your ordering logic if you like
    items.sort(key=lambda x: (x.message_count, x.thread_id), reverse=True)
    return ThreadsResponse(threads=items)


@app.post("/threads", response_model=CreateThreadResponse, status_code=201)
def create_thread() -> CreateThreadResponse:
    tid = uuid.uuid4().hex
    # not persisted until first message; that’s OK
    return CreateThreadResponse(thread_id=tid)


@app.patch("/threads/{thread_id}/title", status_code=204)
def rename_thread(thread_id: str, body: RenameThreadRequest):
    titles = _load_titles()
    titles[thread_id] = body.title.strip()
    _save_titles(titles)
    return  # 204


@app.get("/threads/{thread_id}/messages", response_model=ThreadMessagesResponse)
def get_thread_messages(
    thread_id: str, user_id: str = Query(...)
) -> ThreadMessagesResponse:
    messages = _load_messages(thread_id, user_id)
    title = _pretty_title_from_messages(thread_id, messages)
    serialized = _serialize_messages(messages)
    return ThreadMessagesResponse(
        thread_id=thread_id,
        title=title,
        messages=[MessageDTO(**m) for m in serialized],
    )


# -------- chat --------
# @app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest, github_token: str | None = None) -> ChatResponse:
    thread_id = _to_thread_id(request.thread_id)
    user_id = request.user_id
    user_plan = get_user_plan(user_id)

    config: dict[str, Any] = {
        "configurable": {
            "thread_id": thread_id,
            "user_id": user_id,
            "plan": user_plan,
            "github_token": github_token,
        },
        "metadata": {
            "thread_id": thread_id,
            "user_id": user_id,
            "plan": user_plan,
            "has_github_token": bool(github_token),
        },
        "run_name": "chat_turn",
    }

    events = chatbot.stream(
        {
            "messages": [HumanMessage(content=request.message)],
            "plan": user_plan,
        },
        config=config,
        stream_mode="messages",
    )

    reply_chunks: list[str] = []
    for chunk, _meta in events:
        if getattr(chunk, "type", None) == "ai":
            reply_chunks.append(chunk.content or "")

    full_reply = "".join(reply_chunks).strip()

    # sync history
    msgs = _load_messages(thread_id, user_id)
    if not full_reply:
        ser = _serialize_messages(msgs)
        if ser and ser[-1]["role"] == "assistant":
            full_reply = ser[-1]["content"]

    if not full_reply:
        raise HTTPException(500, "Assistant response was empty")

    # compute (or recall) title
    title = _pretty_title_from_messages(thread_id, msgs)

    return ChatResponse(
        thread_id=thread_id,
        title=title,
        reply=full_reply,
        messages=[MessageDTO(**m) for m in _serialize_messages(msgs)],
    )

# helpers (put near your other helpers)
def _unique_target_dir_from_filename(filename: str) -> Path:
    """
    Make a folder name from the uploaded zip's filename (without extension).
    If the folder exists, add a numeric suffix: name1, name2, ...
    Returns the created directory path.
    """
    base = Path(filename).stem  # "practice.zip" -> "practice"
    # normalize a bit but keep readable
    base = base.strip().rstrip(".")
    if not base:
        base = "upload"

    # try base, base1, base2, ...
    candidate = UPLOAD_STORAGE_ROOT / base
    i = 1
    while candidate.exists():
        candidate = UPLOAD_STORAGE_ROOT / f"{base}{i}"
        i += 1

    candidate.mkdir(parents=True, exist_ok=False)
    return candidate

# -------- uploads & docs --------
@app.post("/uploads", response_model=UploadResponse, status_code=201)
async def upload_project_archive(file: UploadFile = File(...)) -> UploadResponse:
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File name is required.")
    if not file.filename.lower().endswith(".zip"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only .zip archives are supported.")

    # choose a unique extraction directory from the zip's filename
    target_dir = _unique_target_dir_from_filename(file.filename)
    upload_id = target_dir.name  # e.g. "practice", "practice1", ...

    # store the raw zip OUTSIDE the extracted folder to avoid it showing up in the contents
    zips_dir = (UPLOAD_STORAGE_ROOT / "incoming_zips")
    zips_dir.mkdir(parents=True, exist_ok=True)
    temp_zip_path = (zips_dir / f"{upload_id}.zip").resolve()

    try:
        # write uploaded file to a temp zip path
        with temp_zip_path.open("wb") as buffer:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                buffer.write(chunk)

        if temp_zip_path.stat().st_size == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Uploaded archive is empty.")

        # extract into the target directory
        try:
            with zipfile.ZipFile(temp_zip_path) as archive:
                _safe_extract(archive, target_dir)
        except zipfile.BadZipFile as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Uploaded file is not a valid ZIP archive"
            ) from exc

        files, top_level = _summarize_folder(target_dir)
        if not files:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Archive did not contain any files.")

    except ValueError as exc:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except HTTPException:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process archive: {exc}"
        ) from exc
    finally:
        # delete the temp zip after extraction (keep it by commenting the next line)
        temp_zip_path.unlink(missing_ok=True)

    preview = files[:MAX_PREVIEW_FILES]
    return UploadResponse(
        upload_id=upload_id,          # the final folder name (practice, practice1, ...)
        file_name=file.filename,
        total_files=len(files),
        top_level=top_level,
        preview_files=preview,
        folder_path=str(target_dir),  # e.g. .../uploaded_projects/practice2
    )


# @app.post("/uploads", response_model=UploadResponse, status_code=201)
# async def upload_project_archive(file: UploadFile = File(...)) -> UploadResponse:
#     if not file.filename:
#         raise HTTPException(status.HTTP_400_BAD_REQUEST, "File name is required.")
#     if not file.filename.lower().endswith(".zip"):
#         raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only .zip archives are supported.")

#     # ✅ choose directory name from the zip's filename, with numeric suffix if needed
#     target_dir = _unique_target_dir_from_filename(file.filename)
#     upload_id = target_dir.name  # ✅ use the resolved folder name as the id (optional but handy)

#     # ✅ save the uploaded zip as <foldername>.zip inside that folder
#     archive_path = target_dir / f"{upload_id}.zip"

#     try:
#         with archive_path.open("wb") as buffer:
#             while True:
#                 chunk = await file.read(1024 * 1024)
#                 if not chunk:
#                     break
#                 buffer.write(chunk)

#         if archive_path.stat().st_size == 0:
#             raise HTTPException(status.HTTP_400_BAD_REQUEST, "Uploaded archive is empty.")

#         try:
#             with zipfile.ZipFile(archive_path) as archive:
#                 _safe_extract(archive, target_dir)
#         except zipfile.BadZipFile as exc:
#             raise HTTPException(status.HTTP_400_BAD_REQUEST, "Uploaded file is not a valid ZIP archive") from exc

#         files, top_level = _summarize_folder(target_dir)
#         if not files:
#             raise HTTPException(status.HTTP_400_BAD_REQUEST, "Archive did not contain any files.")
#     except ValueError as exc:
#         shutil.rmtree(target_dir, ignore_errors=True)
#         raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
#     except HTTPException:
#         shutil.rmtree(target_dir, ignore_errors=True)
#         raise
#     except Exception as exc:
#         shutil.rmtree(target_dir, ignore_errors=True)
#         raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to process archive: {exc}") from exc
#     finally:
#         # ✅ keep the zip file; if you'd rather delete after extraction, uncomment the next line:
#         # archive_path.unlink(missing_ok=True)
#         pass

#     preview = files[:MAX_PREVIEW_FILES]
#     return UploadResponse(
#         upload_id=upload_id,                 # ✅ now matches the final folder name (practice, practice1, …)
#         file_name=file.filename,
#         total_files=len(files),
#         top_level=top_level,
#         preview_files=preview,
#         folder_path=str(target_dir),         # e.g., .../uploaded_projects/practice2
#     )


@app.post("/uploads/{upload_id}/docs", response_model=DocGenerationResponse)
def generate_docs_for_upload(upload_id: str) -> DocGenerationResponse:
    result = generate_project_docs_impl(upload_id)
    if result.get("status") != "success":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=result.get("message", "Doc generation failed."))

    return DocGenerationResponse(
        upload_id=upload_id,
        status="success",
        readme=result.get("readme"),
        user_manual=result.get("user_manual"),
        files_analyzed=result.get("files_analyzed", 0),
        folder_path=result.get("folder_path", ""),
        readme_path=result.get("readme_path"),
        user_manual_path=result.get("user_manual_path"),
        fallback_used=result.get("fallback_used", False),
    )


# -------- health --------
@app.get("/healthz")
def health_check() -> dict[str, str]:
    return {"status": "ok"}

@app.post("/chat/stream")
def chat_stream(chat_req: ChatRequest, request: Request):
    """
    Streaming wrapper around the existing /chat endpoint.
    """
    def event_generator():
        try:
            # 🔹 Grab GitHub token from header sent by the frontend
            github_token = request.headers.get("x-github-token")

            # 🔹 Pass it into chat(), which will add it to the LangGraph config
            resp = chat(chat_req, github_token=github_token)  # ChatResponse

            thread_id = resp.thread_id
            title = resp.title
            reply = resp.reply or ""

            messages = [
                {"role": m.role, "content": m.content}
                for m in resp.messages
            ]

            if not reply:
                raise RuntimeError("Assistant response was empty in streaming mode.")

            chunk_size = 40
            for i in range(0, len(reply), chunk_size):
                piece = reply[i : i + chunk_size]

                yield (
                    json.dumps(
                        {
                            "event": "token",
                            "thread_id": thread_id,
                            "content": piece,
                        }
                    )
                    + "\n"
                )
                time.sleep(0.02)

            yield (
                json.dumps(
                    {
                        "event": "end",
                        "thread_id": thread_id,
                        "title": title,
                        "reply": reply,
                        "messages": messages,
                    }
                )
                + "\n"
            )

        except Exception as exc:
            yield (
                json.dumps(
                    {
                        "event": "error",
                        "thread_id": getattr(chat_req, "thread_id", None),
                        "message": str(exc),
                    }
                )
                + "\n"
            )

    return StreamingResponse(
        event_generator(),
        media_type="application/x-ndjson",
    )
