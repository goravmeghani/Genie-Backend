from supabase import create_client, Client
import os


SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
# somewhere near the top, reuse your existing client / bucket

PROJECTS_BUCKET = "generated_projects"

def iter_supabase_project_files(
    bucket: str,
    prefix: str,
) -> list[tuple[str, str, bytes]]:
    """
    Recursively list + download all files under `prefix` in a Supabase bucket.

    Returns a list of (remote_path, relative_path_inside_project, file_bytes)
    where relative_path_inside_project strips the project prefix.
    """
    from collections import deque

    prefix = prefix.strip("/")
    queue = deque([prefix])
    root_prefix = prefix + "/"

    files: list[tuple[str, str, bytes]] = []

    while queue:
        folder = queue.popleft()
        items = supabase.storage.from_(bucket).list(folder)

        for item in items or []:
            name = item.get("name")
            if not name:
                continue

            remote_path = f"{folder}/{name}" if folder else name

            # In your project, folders have metadata=None
            if item.get("metadata") is None:
                queue.append(remote_path)
                continue

            # file → download
            data = supabase.storage.from_(bucket).download(remote_path)

            # relative path inside the project root
            rel = remote_path[len(root_prefix) :] if remote_path.startswith(root_prefix) else name

            files.append((remote_path, rel, data))

    return files
