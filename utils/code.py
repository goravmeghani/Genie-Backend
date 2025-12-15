import os
import io
import re
import time
import mimetypes
import zipfile
import subprocess
import shutil
import uuid
import stat
import ctypes
import hashlib
import random
import string
from pathlib import Path
from google import genai
from supabase import create_client, Client
from dotenv import load_dotenv
import io 
PROJECTS_BUCKET = "generated_projects"

load_dotenv()  # load .env into os.environ

def upload_project_to_supabase(
    project_id: str,
    files: list[tuple[str, str]],
    user_id: str | None = None,
    thread_id: str | None = None,
):
    """
    Upload all project files + an in-memory ZIP to Supabase Storage.

    Final structure (always):
      generated_project/{user_id}/{thread_id}/{project_id}/...
    """
    if supabase_client is None:
        raise RuntimeError("Supabase client not initialized")

    if not user_id or not thread_id:
        raise ValueError("user_id and thread_id are required to upload project to Supabase")

    # ✅ your desired prefix
    base_prefix = f"generated_project/{user_id}/{thread_id}/{project_id}"

    print(
        f"\n☁ Uploading project '{project_id}' to Supabase bucket '{PROJECTS_BUCKET}' "
        f"at prefix '{base_prefix}/'..."
    )

    # 1) Upload each source file
    for rel_path, code_text in files:
        bucket_path = f"{base_prefix}/{rel_path.replace(os.sep, '/')}"
        upload_file_to_supabase(bucket_path, code_text)
        print(f"   ☁ Uploaded: {bucket_path}")

    # 2) Add .gitignore
    gitignore_content = (
        "node_modules/\n"
        "dist/\n"
        ".env\n"
        ".vscode/\n"
        ".DS_Store\n"
    )
    gitignore_path = f"{base_prefix}/.gitignore"
    upload_file_to_supabase(gitignore_path, gitignore_content)
    print(f"   ☁ Uploaded: {gitignore_path}")

    # 3) ZIP everything and upload
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zipf:
        for rel_path, code_text in files:
            zipf.writestr(rel_path.replace(os.sep, "/"), code_text)
        zipf.writestr(".gitignore", gitignore_content)

    zip_buf.seek(0)
    zip_bucket_path = f"{base_prefix}/{project_id}.zip"
    upload_file_to_supabase(zip_bucket_path, zip_buf.read())
    print(f"   ☁ Uploaded ZIP: {zip_bucket_path}")

    print(f"\n🌐 Supabase project key: {base_prefix}")
    print("   → Store this base_prefix in your DB to later fetch/deploy this project.")

def list_projects_for_user_thread(user_id: str, thread_id: str) -> list[str]:
    """
    List all generated project IDs (slugs) stored for a specific user and thread.
    Projects are structured as: generated_project/{user_id}/{thread_id}/{project_id}/...
    
    Returns a list of unique project_ids (folder names).
    """
    if supabase_client is None:
        raise RuntimeError("Supabase client not initialized")

    # Define the prefix to search within this user's thread folder
    base_prefix = f"generated_project/{user_id}/{thread_id}/"
    
    # Use the list method with a delimiter to list only the 'folders' 
    # (project_id slugs) directly under the base_prefix.
    # Note: Supabase's 'list' uses the key 'name' for the object or folder name
    # and 'prefixes' for the 'directories' found when using a delimiter.
    # We will list all files and manually extract the project folder names.
    
    resp = supabase_client.storage.from_(PROJECTS_BUCKET).list(base_prefix)
    
    project_ids: set[str] = set()
    
    if not resp:
        return []

    for item in resp:
        name = item.get("name")
        if not name:
            continue
        

        
        parts = name.split('/', 1)
        project_slug = parts[0]

        if '.' not in project_slug: 
            project_ids.add(project_slug)

    return sorted(list(project_ids))

# ------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
client = genai.Client(api_key=GOOGLE_API_KEY)

MODEL_NAME = "gemini-2.5-flash"

# ===========================
# Supabase Storage + Gemini
# ===========================
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
PROJECTS_BUCKET = "generated_projects"

if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
    print("⚠ SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set – Supabase tools will fail.")

supabase_client: Client | None = None
try:
    if SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY:
        supabase_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
except Exception as e:
    print("⚠ Failed to init Supabase client:", e)
    supabase_client = None

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
gemini_client = None
GEMINI_MODEL_NAME = "gemini-2.5-flash"

if not GOOGLE_API_KEY:
    print("⚠ GOOGLE_API_KEY not set – Gemini tools will fail.")
else:
    try:
        gemini_client = genai.Client(api_key=GOOGLE_API_KEY)
    except Exception as e:
        print("⚠ Failed to init Gemini client:", e)
        gemini_client = None

def upload_file_to_supabase(path_in_bucket: str, content: str | bytes) -> None:
    """
    Upload a file into the generated_projects bucket.
    Example path_in_bucket: 'userId/threadId/projectSlug/src/App.jsx'
    """
    if supabase_client is None:
        raise RuntimeError("Supabase client not initialized")

    if isinstance(content, str):
        data = content.encode("utf-8")
    else:
        data = content

    mime_type, _ = mimetypes.guess_type(path_in_bucket)
    mime_type = mime_type or "text/plain"

    supabase_client.storage.from_(PROJECTS_BUCKET).upload(
        path_in_bucket,
        data,
        {
            "content-type": mime_type,
            "x-upsert": "true",  # overwrite if exists
        },
    )

def list_project_files(prefix: str) -> list[str]:
    """
    List all file names under a given prefix in the bucket.
    Example prefix: 'userId/threadId/projectSlug/'
    """
    if supabase_client is None:
        raise RuntimeError("Supabase client not initialized")

    # Supabase returns items with "name" key
    resp = supabase_client.storage.from_(PROJECTS_BUCKET).list(prefix)
    if not resp:
        return []
    return [item["name"] for item in resp]




# ============================================================
# PERMISSION UTILITIES
# ============================================================
def force_write_permissions(path):
    try:
        if os.name == "nt":
            FILE_ATTRIBUTE_NORMAL = 0x80
            ctypes.windll.kernel32.SetFileAttributesW(str(path), FILE_ATTRIBUTE_NORMAL)
        else:
            os.chmod(path, 0o777)
    except Exception as e:
        print(f"⚠ Permission reset failed for {path}: {e}")

def remove_readonly(func, path, excinfo):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


# ============================================================
# HELPERS: folder management & file save
# ============================================================
def create_unique_folder(base_name="generated_project"):
    unique_id = uuid.uuid4().hex[:6]
    folder = f"{base_name}_{unique_id}"
    os.makedirs(folder, exist_ok=True)
    force_write_permissions(folder)
    return folder

def prepare_folder(folder):
    if os.path.exists(folder):
        force_write_permissions(folder)
        shutil.rmtree(folder, onerror=remove_readonly)
    os.makedirs(folder, exist_ok=True)
    force_write_permissions(folder)
    return folder


def save_code_files(markdown: str, output_folder: str):
    """
    Parse LLM output of the form:

    File: path/to/file.ext
    ```language
    // code here
    ```

    and write each file under output_folder.
    """
    file_pattern = re.compile(
        r"File:\s*([^\n]+?)\s*\n```[a-zA-Z0-9]*\n(.*?)```",
        re.DOTALL,
    )

    matches = file_pattern.findall(markdown)
    files_created: list[str] = []

    if not matches:
        # fallback: dump everything into one markdown file so we see what came back
        fallback_file = os.path.join(output_folder, "project_raw.md")
        with open(fallback_file, "w", encoding="utf-8") as f:
            f.write(markdown)
        print("⚠ No file blocks parsed. Wrote raw LLM output to project_raw.md")
        return [fallback_file]

    for filename, code_content in matches:
        filename = filename.strip()

        # skip empty / weird names
        if not filename:
            print("⏭ Skipping empty filename block")
            continue

        # skip directories like "src/" etc.
        if filename.endswith("/") or filename.endswith("\\"):
            print(f"⏭ Skipping directory: {filename}")
            continue

        # Require an extension (package.json, src/App.jsx, etc.)
        if "." not in os.path.basename(filename):
            print(f"⏭ Skipping invalid file without extension: {filename}")
            continue

        full_path = os.path.join(output_folder, filename)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        force_write_permissions(os.path.dirname(full_path))

        with open(full_path, "w", encoding="utf-8") as f:
            f.write(code_content.strip() + "\n")

        force_write_permissions(full_path)
        files_created.append(full_path)
        print(f"✅ Wrote file: {full_path}")

    return files_created


# ============================================================
# ZIP + NPM HELPERS
# ============================================================
def zip_folder(folder_name):
    zip_filename = f"{folder_name}.zip"
    with zipfile.ZipFile(zip_filename, "w", zipfile.ZIP_DEFLATED) as zipf:
        for root, _, files in os.walk(folder_name):
            for file in files:
                file_path = os.path.join(root, file)
                zipf.write(file_path, os.path.relpath(file_path, folder_name))
    return zip_filename

def install_dependencies(folder):
    print("📦 Installing npm dependencies...")
    subprocess.run("npm install", cwd=folder, shell=True, check=True)
    print("✅ Dependencies installed.")

def run_dev_server(folder):
    print("🚀 Launching Vite dev server in a new terminal window...")
    if os.name == "nt":  # Windows
        subprocess.Popen(
            f'start cmd /k "cd {folder} && npm run dev"',
            shell=True
        )
    else:  # macOS / Linux
        subprocess.Popen(
            ["sh", "-c", f'cd "{folder}" && npm run dev'],
        )
    print("🌐 Dev server running in a separate terminal. You can continue using this script.")



def parse_code_files(markdown: str) -> list[tuple[str, str]]:
    """
    Parse LLM output of the form:

    File: path/to/file.ext
    ```language
    // code here
    ```

    and return a list of (relative_path, code_text).
    """
    file_pattern = re.compile(
        r"File:\s*([^\n]+?)\s*\n```[a-zA-Z0-9]*\n(.*?)```",
        re.DOTALL,
    )

    matches = file_pattern.findall(markdown)
    files: list[tuple[str, str]] = []

    if not matches:
        print("⚠ No file blocks parsed in LLM output.")
        return []

    for filename, code_content in matches:
        filename = filename.strip()

        if not filename:
            print("⏭ Skipping empty filename block")
            continue

        if filename.endswith("/") or filename.endswith("\\"):
            print(f"⏭ Skipping directory: {filename}")
            continue

        if "." not in os.path.basename(filename):
            print(f"⏭ Skipping invalid file without extension: {filename}")
            continue

        files.append((filename, code_content.strip() + "\n"))
        print(f"📝 Parsed file: {filename}")

    return files


def generate_project(
    idea: str,
    project_name: str | None = None,
    user_id: str | None = None,
    thread_id: str | None = None,
) -> dict:

    """
    Non-interactive generator used by tools.

    - idea: what to build (dashboard description, etc.)
    - project_name: base name for the project

    Returns:
        {
          "project_id": str,
          "bucket": str,
          "prefix": str,
          "files": [str],
        }
    """
    if not idea.strip():
        raise ValueError("Project idea cannot be empty.")
    
    if not user_id or not thread_id:
        raise ValueError("user_id and thread_id are required for project generation.")

    if gemini_client is None:
        raise RuntimeError("Gemini client not configured (GOOGLE_API_KEY missing).")

    raw_name = (project_name or "project").strip() or "project"

    # Simple slug + unique id for project_id
    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw_name).strip("-") or "project"
    unique_id = uuid.uuid4().hex[:6]
    project_id = f"{safe_name}_{unique_id}"      # e.g. "hestin_800f31"
    # 👇 compute the base_prefix that both upload + return will use
    if user_id and thread_id:
        base_prefix = f"{user_id}/{thread_id}/{project_id}"
    else:
        base_prefix = project_id

    prompt_text = f"""
  You are an expert React front-end engineer.
The user wants to build the following project:
{idea}

IMPORTANT RULES — MUST FOLLOW:
1. Generate a complete, fully functional React.js + Vite project.
2. NO backend, no Next.js, no Vue, no Angular, no Python.
3. Styling MUST use Tailwind CSS only.
4. The project MUST run successfully with:
     npm install
     npm run dev
5. You are allowed to create ANY number of files and folders
   (components, pages, hooks, utils, layouts, assets, context, etc.).
6. The project must be fully structured, functional, and production-quality.
7. No placeholders. No TODOs. Provide full working code.

OUTPUT FORMAT — STRICT:
For EVERY file you generate, follow exactly this format:

File: path/to/file.ext
```language
(code here)
No explanations, no extra comments outside code blocks, no missing files.
"""

    print(f"\n⚙ Generating project '{project_id}' with Gemini...\n")
    response = gemini_client.models.generate_content(
        model=GEMINI_MODEL_NAME,
        contents=[prompt_text],
    )
    markdown_text = getattr(response, "text", None) or getattr(response, "output_text", None) or str(response)

    print("💾 Parsing project files from LLM output...")
    files = parse_code_files(markdown_text)

    if not files:
        raise RuntimeError("No files parsed from LLM output.")

    # Upload everything directly to Supabase
    upload_project_to_supabase(
        project_id,
        files,
        user_id=user_id,
        thread_id=thread_id,
    )

    print(f"\n✅ Project '{project_id}' created and uploaded to Supabase.")
    return {
        "project_id": project_id,
        "bucket": PROJECTS_BUCKET,
        "prefix": f"{base_prefix}/",           # <-- full prefix: user/thread/project_id/
        "files": [rel for (rel, _) in files],
    }

def build_project():
    """
    Old interactive CLI wrapper, now just calls generate_project().
    Still useful if you run: python code.py
    """
    print("👋 Hi! Let's build something together.")

    def multiline_input(prompt):
        print(prompt + " (Press ENTER twice to finish):")
        lines = []
        while True:
            line = input()
            if line == "":
                break
            lines.append(line)
        return "\n".join(lines)

    idea = multiline_input("💡 Describe what you want to build")
    raw_name = input("🧱 Project name: ").strip() or "project"

    try:
        result = generate_project(idea=idea, project_name=raw_name)
        print("\n=== PROJECT SUMMARY ===")
        print("Project ID:", result["project_id"])
        print("Bucket:    ", result["bucket"])
        print("Prefix:    ", result["prefix"])
        print("Files:")
        for rel in result["files"]:
            print(" -", rel)
    except Exception as e:
        print("❌ Error generating project:", e)


# ============================================================
# ENTRY
# ============================================================
if __name__ == "__main__":
    build_project()
