
from __future__ import annotations

import os
from fastapi import APIRouter, HTTPException, Query


from supabase import create_client, Client  # 👈 add this

# from test_supabase_download import download_folder   # 👈 no longer needed


projects_router = APIRouter(prefix="/projects", tags=["projects"])

SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
SUPABASE_BUCKET = "generated_projects"


def _list_objects_recursive(prefix: str) -> list[dict]:
    """
    List all Supabase objects under a prefix.
    Returns entries like:
    { "name": "src/App.jsx", "id": ..., "updated_at": ..., "metadata": ... }
    """
    # Supabase list() is not recursive by default; we simulate recursion.
    results: list[dict] = []

    def walk(path: str):
        items = supabase.storage.from_(SUPABASE_BUCKET).list(
            path,
            {
                "limit": 1000,
                "offset": 0,
                "sortBy": {"column": "name", "order": "asc"},
            },
        )
        # items are dicts with keys: name, id, updated_at, created_at, last_accessed_at, metadata
        for item in items:
            name = item["name"]
            full = f"{path}/{name}" if path else name

            # Heuristic: if it has a dot, treat as file; otherwise could be folder
            if "." in name:
                results.append({"name": full})
            else:
                # This may be a folder; go deeper
                walk(full)

    walk(prefix.rstrip("/"))
    return results


def _build_tree_from_keys(prefix: str, keys: list[str]) -> list[dict]:
    """
    keys: list like ["src/App.jsx", "src/main.jsx", "package.json"]
    Return nested tree in same format you already use in FE:
    [{ type, name, path, children? }, ...]
    """
    root: dict = {"children": {}}

    for key in keys:
        rel = key[len(prefix) :].lstrip("/") if key.startswith(prefix) else key
        if not rel:
            continue
        parts = rel.split("/")
        node = root
        accumulated = ""

        for i, part in enumerate(parts):
            accumulated = f"{accumulated}/{part}" if accumulated else part
            is_file = (i == len(parts) - 1)

            if "children" not in node:
                node["children"] = {}

            children = node["children"]
            if part not in children:
                if is_file:
                    children[part] = {
                        "type": "file",
                        "name": part,
                        "path": accumulated.replace("\\", "/"),
                    }
                else:
                    children[part] = {
                        "type": "folder",
                        "name": part,
                        "path": accumulated.replace("\\", "/"),
                        "children": {},
                    }
            node = children[part]

    def to_list(node: dict) -> list[dict]:
        result: list[dict] = []
        for name, child in sorted(node.get("children", {}).items(), key=lambda kv: (kv[1]["type"] == "file", kv[0].lower())):
            if child["type"] == "folder":
                result.append(
                    {
                        "type": "folder",
                        "name": child["name"],
                        "path": child["path"],
                        "children": to_list(child),
                    }
                )
            else:
                result.append(
                    {
                        "type": "file",
                        "name": child["name"],
                        "path": child["path"],
                    }
                )
        return result

    return to_list(root)


@projects_router.get("/{project_id}/file-tree")
def get_project_file_tree(
    project_id: str,
    user_id: str = Query(...),
    thread_id: str = Query(...),
):
    """
    Build a file tree directly from Supabase object keys.
    No temp dirs, no local extraction.
    """
    # prefix used when you store generated projects in Supabase
    # e.g. "generated_project/<user_id>/<thread_id>/<project_id>/"
    prefix = f"generated_project/{user_id}/{thread_id}/{project_id}".rstrip("/") + "/"

    objects = _list_objects_recursive(prefix)

    if not objects:
        raise HTTPException(
            status_code=404,
            detail=f"No files found in Supabase for project '{project_id}'.",
        )

    keys = [obj["name"] for obj in objects]
    tree = _build_tree_from_keys(prefix, keys)

    return {
        "project_id": project_id,
        "files": tree,
    }

@projects_router.get("/{project_id}/file-content")
def get_project_file_content(
    project_id: str,
    path: str = Query(..., description="Relative path inside project, e.g. src/App.jsx"),
    user_id: str = Query(...),
    thread_id: str = Query(...),
):
    """
    Read a single file directly from Supabase (no local download).
    """
    # Rebuild prefix
    base_prefix = f"generated_project/{user_id}/{thread_id}/{project_id}".rstrip("/")
    object_path = f"{base_prefix}/{path.lstrip('/')}".replace("\\", "/")

    try:
        res = supabase.storage.from_(SUPABASE_BUCKET).download(object_path)
    except Exception as exc:
        raise HTTPException(
            status_code=404,
            detail=f"File not found in Supabase: {path} ({exc})",
        ) from exc

    if res is None:
        raise HTTPException(
            status_code=404,
            detail=f"File not found in Supabase: {path}",
        )

    # supabase-py returns bytes
    try:
        text = res.decode("utf-8", errors="replace")
    except AttributeError:
        # if res is already str
        text = str(res)

    return {
        "project_id": project_id,
        "path": path,
        "content": text,
    }
