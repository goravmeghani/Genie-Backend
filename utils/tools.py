import os
import json
import base64
import subprocess
import time
import requests
from botocore.exceptions import ClientError

# Third-party libraries
import boto3
from dotenv import load_dotenv
from pydantic import Field
from github import Auth, Github, GithubException
import github
from supabase import create_client, Client

# LangChain/LangGraph imports
from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig

from utils.state import GitHubState
from utils.code import generate_project, list_projects_for_user_thread, upload_file_to_supabase
from utils.supabase_iteration import iter_supabase_project_files
from utils.doc_gen_functionality import get_model_for, summarize_many, generate_docs, _fallback_generate_docs_from_paths


load_dotenv()
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")

SUMMARIZER_MODEL = os.getenv("MODEL_SUMMARIZER")
DOCGEN_MODEL = os.getenv("MODEL_DOCGEN")

DOCGEN_FILE_LIMIT = 200

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)




def _get_client(pat:str) -> Github:
    if not pat:
        raise ValueError("No GitHub PAT provided in GITHUB_TOKEN env var")
    return Github(auth=github.Auth.Token(pat))

# -------------------
# Tools
# -------------------
# -------------------
# 1.Github Tools
# -------------------
@tool
def get_username(
    pat: str = Field(..., description="Personal Access Token for Github"), # <--- ADDED
) -> dict:
    """Get the GitHub username of the authenticated user with the help of Personal Access Token."""
    try:
        g = _get_client(pat)
        username = g.get_user().login
        return {"status": "success", "username": username, "message": f"Successfully retrieved GitHub username: **{username}**."}
    except Exception as e:
        return {"status": "error", "message": f"Failed to retrieve username. Please check the Personal Access Token. Error: {e}"}

@tool
def list_repos(pat: str = Field(..., description="Personal Access Token for Github"),) -> dict:
    """List all repositories for the authenticated user."""
    g = _get_client(pat)
    user = g.get_user()
    repos = [r.name for r in user.get_repos()]
    return {"repos": repos}

@tool
def list_branches(state: GitHubState, pat: str = Field(..., description="Personal Access Token for Github"),) -> dict:
    """List branches in the active repository."""
    g = _get_client(pat)
    if not state.get("active_repo"):
        return {"error": "No active repo set"}
    repo = g.get_user().get_repo(state["active_repo"])
    branches = [b.name for b in repo.get_branches()]
    return {"branches": branches}

@tool
def set_active_repo(state: GitHubState, repo_name: str, pat: str = Field(..., description="Personal Access Token for Github"),) -> dict:
    """Set the active repository for future operations."""
    g = _get_client(pat)
    user = g.get_user()
    state["active_repo"] = repo_name
    return {"active_repo": repo_name}

@tool
def set_active_branch(state: GitHubState, branch_name: str, pat: str = Field(..., description="Personal Access Token for Github"),) -> dict:
    """Set the active branch for future operations."""
    g = _get_client(pat)
    if not state.get("active_repo"):
        return {"error": "No active repo set"}
    state["active_branch"] = branch_name
    return {"active_branch": branch_name}


@tool
def create_branch(state: GitHubState, new_branch: str, from_branch: str = "main",pat: str = Field(..., description="Personal Access Token for Github"),) -> dict:
    """Create a new branch from an existing branch (default: main)."""
    g = _get_client(pat)
    if not state.get("active_repo"):
        return {"error": "No active repo set"}
    
    repo = g.get_user().get_repo(state["active_repo"])
    source_branch = repo.get_branch(from_branch)
    repo.create_git_ref(ref=f"refs/heads/{new_branch}", sha=source_branch.commit.sha)
    return {"created_branch": new_branch, "from_branch": from_branch}


@tool
def delete_branch(state: GitHubState, branch_name: str,pat: str = Field(..., description="Personal Access Token for Github"),) -> dict:
    """Delete a branch from the active repository."""
    g = _get_client(pat)
    if not state.get("active_repo"):
        return {"error": "No active repo set"}

    repo = g.get_user().get_repo(state["active_repo"])
    ref = repo.get_git_ref(f"heads/{branch_name}")
    ref.delete()
    return {"deleted_branch": branch_name}


@tool
def rename_branch(state: GitHubState, old_name: str, new_name: str,pat: str = Field(..., description="Personal Access Token for Github"),) -> dict:
    """Rename a branch in the active repository."""
    g = _get_client(pat)
    if not state.get("active_repo"):
        return {"error": "No active repo set"}

    repo = g.get_user().get_repo(state["active_repo"])
    branch = repo.get_branch(old_name)
    branch.rename(new_name)
    return {"renamed": old_name, "to": new_name}


@tool
def get_active_branch(state: GitHubState,pat: str = Field(..., description="Personal Access Token for Github"),) -> dict:
    """Get the currently active branch."""
    if not state.get("active_branch"):
        return {"error": "No active branch set"}
    return {"active_branch": state["active_branch"]}


@tool
def create_repo(name: str, private: bool = False,pat: str = Field(..., description="Personal Access Token for Github"),) -> dict:
    """Create a new repository."""
    g = _get_client(pat)
    user = g.get_user()
    repo = user.create_repo(name=name, private=private)
    return {"repo": repo.name, "private": repo.private}

@tool
def delete_repo(repo_name: str, pat: str = Field(..., description="Personal Access Token for Github"),) -> dict:
    """Delete a repository."""
    g = _get_client(pat)
    repo = g.get_user().get_repo(repo_name)
    repo.delete()
    return {"deleted": repo_name}

@tool
def list_files(state: GitHubState, path: str = "", pat: str = Field(..., description="Personal Access Token for Github"),) -> dict:
    """List all files (recursively) in the active repository."""
    g = _get_client(pat)
    if not state.get("active_repo"):
        return {"error": "No active repo set"}
    repo = g.get_user().get_repo(state["active_repo"])

    files = []
    contents = repo.get_contents(path)
    while contents:
        item = contents.pop(0)
        if item.type == "dir":
            contents.extend(repo.get_contents(item.path))
        else:
            files.append(item.path)

    return {"files": files}

@tool
def create_file(state: GitHubState, path: str, content: str, message: str,pat: str = Field(..., description="Personal Access Token for Github"),) -> dict:
    """Create a new file in the active repository."""
    g = _get_client(pat)
    if not state.get("active_repo"):
        return {"error": "No active repo set"}
    repo = g.get_user().get_repo(state["active_repo"])
    repo.create_file(path, message, content)
    return {"created": path, "message": message}

@tool
def read_file(state: GitHubState, path: str,pat: str = Field(..., description="Personal Access Token for Github"),) -> dict:
    """Read a file from the active repository"""
    g = _get_client(pat)
    if not state.get("active_repo"):
        return {"error": "No active repo set"}
    repo = g.get_user().get_repo(state["active_repo"])
    content = repo.get_contents(path)
    return {"path": path, "content": content.decoded_content.decode("utf-8")}


@tool
def delete_file(state: GitHubState, path: str, message: str, pat: str = Field(..., description="Personal Access Token for Github"),) -> dict:
    """Delete a file from the active repository."""
    g = _get_client(pat)
    if not state.get("active_repo"):
        return {"error": "No active repo set"}
    repo = g.get_user().get_repo(state["active_repo"])
    file = repo.get_contents(path)
    repo.delete_file(path, message, file.sha)
    return {"deleted": path, "message": message}


# -------------------
# 2.Supabase + React Project Tools

@tool
def generate_react_project_to_supabase(
    idea: str,
    user_id: str = Field(..., description="The unique ID of the current user."), # <--- ADDED
    thread_id: str = Field(..., description="The ID of the current chat thread."), # <--- ADDED
    project_name: str = "project",
    config: RunnableConfig | None = None, # config is now mostly redundant here
) -> dict:
    """
    Generate a React + Vite + Tailwind project using Gemini
    and upload all files + a ZIP to the Supabase 'generated_projects' bucket.

    Supabase layout:
      {user_id}/{thread_id}/{project_id}/...

    user_id and thread_id are taken from LangGraph config.configurable.
    """
    print("generate_react_project_to_supabase called with:", user_id, thread_id)
    try:
        result = generate_project(
            idea=idea,
            project_name=project_name,
            user_id=user_id,
            thread_id=thread_id,
        )
        return {
            "status": "success",
            "project_id": result["project_id"],
            "bucket": result["bucket"],
            "prefix": result["prefix"],  # e.g. "userId/threadId/projectId/"
            "files": result["files"],
        }
    except Exception as exc:
        return {
            "status": "error",
            "message": str(exc),
        }
    
@tool
def list_generated_projects(
    user_id: str = Field(..., description="The unique ID of the current user."),
    thread_id: str = Field(..., description="The ID of the current chat thread."),
) -> dict:
    """
    Lists all React project IDs (folder names/slugs) that were generated
    and stored in Supabase for the current user and chat thread.
    """
    # The required tool arguments (user_id, thread_id) ensure the LLM provides context.
    try:
        # Assume list_projects_for_user_thread is imported/available from code.py
        projects = list_projects_for_user_thread(user_id, thread_id) 
        
        if not projects:
            return {
                "status": "empty",
                "message": f"No projects found for user '{user_id}' in thread '{thread_id}'.",
                "projects": [],
            }
    
        return {
            "status": "success",
            "total_projects": len(projects),
            "projects": projects,
        }
    except Exception as exc:
         return {
            "status": "error",
            "message": f"Failed to list projects: {exc}",
         }
    

PROJECTS_BUCKET = "generated_projects"
@tool
def upload_supabase_project_to_github(
    state: GitHubState,
    user_id: str = Field(..., description="The unique ID of the current user."),
    thread_id: str = Field(..., description="The ID of the current chat thread."),
    project_id: str = Field(..., description="The project folder/slug inside Supabase."),
    commit_message: str = "Uploaded Supabase project to GitHub",
    pat: str = Field(..., description="Personal Access Token for Github"),
) -> dict:
    """
    Upload a generated project from Supabase directly to the active GitHub repo
    WITHOUT writing to local disk.

    Supabase layout:
      generated_project/{user_id}/{thread_id}/{project_id}/...
    """
    token = pat
    if not token:
        return {"status": "error", "message": "GitHub personal access token is missing in state."}
    repo_name = state.get("active_repo")
    if not repo_name:
        return {"status": "error", "message": "No active repository set. Call set_active_repo first."}

    client = _get_client(token)
    try:
        repo = client.get_user().get_repo(repo_name)
    except GithubException as exc:
        return {"status": "error", "message": f"Unable to access repository '{repo_name}': {exc.data or str(exc)}"}

    branch = state.get("active_branch") or repo.default_branch

    # Supabase prefix for this project
    prefix = f"generated_project/{user_id}/{thread_id}/{project_id}"
    bucket = PROJECTS_BUCKET

    print(f"📦 Direct Supabase→GitHub upload")
    print(f"   bucket: {bucket}")
    print(f"   prefix: {prefix}")

    try:
        file_entries = iter_supabase_project_files(bucket, prefix)
    except Exception as exc:
        return {
            "status": "error",
            "message": f"Failed to list/download files from Supabase: {exc}",
        }

    if not file_entries:
        return {
            "status": "error",
            "message": f"No files found under {prefix} in bucket {bucket}",
        }

    uploaded: list[str] = []

    for remote_path, rel_path, file_bytes in file_entries:
        try:
            encoded_content = base64.b64encode(file_bytes).decode("utf-8")
        except Exception as exc:
            return {"status": "error", "message": f"Failed to encode {rel_path}: {exc}"}

        msg = f"{commit_message}: {rel_path}"
        api_url = f"https://api.github.com/repos/{repo.full_name}/contents/{rel_path}"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
        }

        # Check if file exists (to decide create/update)
        get_resp = requests.get(api_url, headers=headers, params={"ref": branch})
        if get_resp.status_code == 200:
            existing_sha = get_resp.json().get("sha")
            payload = {
                "message": msg,
                "content": encoded_content,
                "branch": branch,
                "sha": existing_sha,
            }
            action = "updated"
        elif get_resp.status_code == 404:
            payload = {
                "message": msg,
                "content": encoded_content,
                "branch": branch,
            }
            action = "created"
        else:
            return {
                "status": "error",
                "message": f"Failed to check {rel_path}: {get_resp.text}",
            }

        put_resp = requests.put(api_url, headers=headers, json=payload)
        if put_resp.status_code not in (200, 201):
            return {
                "status": "error",
                "message": f"Failed to upload {rel_path}: {put_resp.text}",
            }

        uploaded.append(f"{action}:{rel_path}")

    return {
        "status": "success",
        "uploaded": uploaded,
        "repository": repo.full_name,
        "branch": branch,
        "source_bucket": bucket,
        "source_prefix": prefix,
    }


@tool
def generate_project_docs_impl(
    project_id: str,
    user_id: str = Field(..., description="The unique ID of the current user."),
    thread_id: str = Field(..., description="The ID of the current chat thread."),
) -> dict:
    """
    Generate README and USER_MANUAL for a Supabase project **directly from Supabase**.

    Supabase layout:
      generated_project/{user_id}/{thread_id}/{project_id}
    """
    if supabase is None:
        return {"status": "error", "message": "Supabase client not configured."}

    bucket = PROJECTS_BUCKET
    prefix = f"generated_project/{user_id}/{thread_id}/{project_id}"

    print(
        f"Generating docs for project '{project_id}' "
        f"under user '{user_id}', thread '{thread_id}', prefix '{prefix}'"
    )

    try:
        file_entries = iter_supabase_project_files(bucket, prefix)
    except Exception as exc:
        return {
            "status": "error",
            "message": f"Failed to list/download files from Supabase: {exc}",
        }

    if not file_entries:
        return {"status": "error", "message": f"No files found for project '{project_id}'."}

    # Convert to (rel_path, bytes) for your summarizer/docgen
    rel_files: list[tuple[str, bytes]] = [(rel, data) for _, rel, data in file_entries]
    files_to_analyze = rel_files[:DOCGEN_FILE_LIMIT]
    fallback_used = False

    try:
        summarizer_llm = get_model_for("summarizer")
        summaries = summarize_many(files_to_analyze, llm=summarizer_llm)

        docgen_llm = get_model_for("docgen")
        docs = generate_docs(
            summaries=summaries,
            readme_template="",        # or load templates as before
            manual_template="",
            llm=docgen_llm,
        )
    except Exception as exc:
        # use fallback that can work from in-memory list
        fallback_used = True
        # you can adapt _fallback_generate_docs to accept (paths, bytes)
        docs = _fallback_generate_docs_from_paths(rel_files, project_id=project_id)
        print(f"⚠️ Docgen failed, using fallback. Reason: {exc}")

    readme_text = docs.get("readme", "")
    user_manual_text = docs.get("user_manual", "")

    # Upload generated docs back to Supabase
    full_prefix = f"{user_id}/{thread_id}/{project_id}"
    readme_bucket_path = f"generated_project/{full_prefix}/README.md"
    user_manual_bucket_path = f"generated_project/{full_prefix}/USER_MANUAL.md"

    upload_file_to_supabase(readme_bucket_path, readme_text)
    upload_file_to_supabase(user_manual_bucket_path, user_manual_text)

    print(f"✅ Docs uploaded to Supabase under prefix: {full_prefix}")

    return {
        "status": "success",
        "files_analyzed": len(files_to_analyze),
        "fallback_used": fallback_used,
        "readme_path": readme_bucket_path,
        "user_manual_path": user_manual_bucket_path,
    }



# def deploy_react_site(state: GitHubState, bucketName: str) -> dict:
@tool
def deploy_react_site(state: GitHubState, bucketName: str,pat: str = Field(..., description="Personal Access Token for Github"), ) -> dict:

    """Deploy a React file to an S3 bucket using Terraform."""
    print("Deploying React site...")
    auth = Auth.Token(pat)
    g = Github(auth=auth)
    repo = g.get_user().get_repo(state["active_repo"])
    
    ALLOWED_EXTENSIONS = [".html", ".css", ".js"]
    # -----------------------------
    # Load environment variables
    # -----------------------------
    terraform_path = os.getenv("TERRAFORM_PATH")  # ✅ Adjust to your terraform.exe path
    aws_region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    # ✅ Path to Terraform configuration directory
    project_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(project_dir)
    print(f"\n🚀 AWS Static Site Deployment Automation\n📂 Using Terraform config from: {project_dir}\n")

    # -----------------------------
    # Helper: Check if bucket exists
    # -----------------------------
    def check_bucket_exists(bucket_name):
        s3 = boto3.client("s3", region_name=aws_region)
        try:
            s3.head_bucket(Bucket=bucket_name)
            return True
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ["404", "NoSuchBucket"]:
                return False
            return True
        
    bucket_name = bucketName.strip()
    if not bucket_name:
        print("⚠️ Please enter a non-empty bucket name.")


    if check_bucket_exists(bucket_name):
        print(f"❌ Bucket name '{bucket_name}' already exists globally. Try another one.")
    else:
        print(f"✅ Bucket name '{bucket_name}' is available.")
    github_repo = repo.clone_url
    print(f"🔗 Using GitHub repo: {github_repo}")

    # -----------------------------
    # Write terraform.tfvars
    # -----------------------------
    tfvars_content = f"""
    bucket_name = "{bucket_name}"
    github_repo = "{github_repo}"
    aws_region  = "{aws_region}"
    """
    tfvars_path = os.path.join(project_dir, "terraform.tfvars")
    with open(tfvars_path, "w") as f:
        f.write(tfvars_content)

    print(f"\n📝 terraform.tfvars created with:\n{tfvars_content}")
    
    # -----------------------------
    # Terraform Workspace Setup
    # -----------------------------
    print(f"\n🧩 Checking/Creating Terraform workspace for '{bucket_name}'...")
    workspace_list = subprocess.run(
        [terraform_path, "workspace", "list"],
        capture_output=True,
        text=True
    ).stdout

    if bucket_name in workspace_list:
        subprocess.run([terraform_path, "workspace", "select", bucket_name], check=True)
        print(f"✅ Switched to existing workspace: {bucket_name}")
    else:
        subprocess.run([terraform_path, "workspace", "new", bucket_name], check=True)
        print(f"🆕 Created and switched to new workspace: {bucket_name}")

    # -----------------------------
    # Terraform Init
    # -----------------------------
    print("\n🧩 Initializing Terraform...")
    subprocess.run([terraform_path, "init"], check=True)

    # -----------------------------
    # Terraform Apply
    # -----------------------------
    print("\n🚧 Applying Terraform to deploy infrastructure...")
    subprocess.run([terraform_path, "apply", "-auto-approve"], check=True)

    print(f"\n✅ Infrastructure deployed successfully for: {bucket_name}")

    # -----------------------------
    # Trigger CodeBuild
    # -----------------------------
    codebuild = boto3.client("codebuild", region_name=aws_region)
    project_name = f"{bucket_name}-build"

    try:
        print("\n🔧 Triggering CodeBuild to start build...")
        response = codebuild.start_build(projectName=project_name)
        build_id = response["build"]["id"]

        print(f"🚀 Build triggered successfully!")
        print(f"🆔 Build ID: {build_id}")
        build_console_url = (
            f"https://console.aws.amazon.com/codesuite/codebuild/projects/"
            f"{project_name}/build/{build_id}/?region={aws_region}"
        )
        print(f"🔗 View in AWS Console:\n{build_console_url}")

    except Exception as e:
        print(f"❌ Failed to trigger CodeBuild: {e}")
        return {"status": "error", "message": str(e)}

    # -----------------------------
    # Wait for build to complete
    # -----------------------------
    def wait_for_build_completion(build_id):
        print("\n⏳ Waiting for CodeBuild to finish...")
        while True:
            try:
                build_info = codebuild.batch_get_builds(ids=[build_id])["builds"][0]
                status = build_info["buildStatus"]
                current_phase = build_info["currentPhase"]
                print(f"   🧱 Status: {status} | Phase: {current_phase}", end="\r")

                if status in ["SUCCEEDED", "FAILED", "FAULT", "STOPPED", "TIMED_OUT"]:
                    print()  # newline
                    return status

                time.sleep(15)
            except Exception as e:
                print(f"\n⚠️ Error checking build status: {e}")
                time.sleep(20)

    build_status = wait_for_build_completion(build_id)

    # -----------------------------
    # Handle build result
    # -----------------------------
    if build_status == "SUCCEEDED":
        print(f"\n✅ Build completed successfully! 🎉")
        print("\n🌍 Fetching website URL from Terraform outputs...")
        try:
            result = subprocess.run(
                [terraform_path, "output", "-json"],
                capture_output=True,
                text=True,
                check=True
            )
            outputs = json.loads(result.stdout)
            site_url = outputs.get("bucket_url", {}).get("value", f"http://{bucket_name}.s3-website-{aws_region}.amazonaws.com")

            print(f"\n👉 Your site is live: {site_url}\n")
            return {"status": "success", "website_url": site_url}
        except Exception as e:
            print(f"⚠️ Could not fetch website URL automatically: {e}")
            print(f"Try manually: http://{bucket_name}.s3-website-{aws_region}.amazonaws.com")

    else:
        print(f"\n❌ Build failed with status: {build_status}")
        print(f"🔗 Check build logs in AWS Console:\n{build_console_url}")

    print("\n🎉 Deployment script complete!\n")

# -------------------
# Register tools (plan-aware)
# -------------------

# Tools that are safe for FREE and PREMIUM users
SAFE_TOOLS = [
    # Supabase / docgen tools
    generate_react_project_to_supabase,
    list_generated_projects,
    generate_project_docs_impl,
]

# Tools that should ONLY be usable by PREMIUM users
PREMIUM_ONLY_TOOLS = [
    # GitHub mutation / repo management
    get_username,
    list_repos,
    create_repo,
    delete_repo,
    set_active_repo,
    list_branches,
    set_active_branch,
    create_branch,
    delete_branch,
    rename_branch,
    get_active_branch,
    list_files,
    create_file,
    read_file,
    delete_file,
    upload_supabase_project_to_github,

    # AWS / deployment
    deploy_react_site,
]

# Full set (used for ToolNode, or for premium plan)
ALL_TOOLS = SAFE_TOOLS + PREMIUM_ONLY_TOOLS

tools = ALL_TOOLS

def get_tools_for_plan(plan: str):
    """
    Return the list of tools the LLM is allowed to call for the given plan.
    This is what we bind into the tool-calling model (tool_llm.bind_tools).
    """
    normalized = (plan or "free").lower()
    if normalized == "premium":
        return ALL_TOOLS
    else:
        # default to free-safe tools
        return SAFE_TOOLS
    
