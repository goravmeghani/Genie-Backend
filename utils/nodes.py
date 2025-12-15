# my_agent/utils/nodes.py

from typing import List, Optional
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage, trim_messages
from langchain_core.runnables import RunnableConfig
from langgraph.prebuilt import ToolNode
import json # Used for JSON dumps
import sys
from utils.state import ChatState
from utils.tools import get_tools_for_plan
from langchain_groq import ChatGroq
import os

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
CHAT_MODEL = os.getenv("GROQ_CHAT_MODEL")
TOOL_MODEL = os.getenv("GROQ_TOOL_MODEL")
MAX_SUMMARY_TOKENS = 300          # hard cap for summary
MAX_LAST_USER_TOKENS = 600       # hard cap for last user message


chat_llm = ChatGroq(
    model=CHAT_MODEL,
    temperature=0,
    api_key=GROQ_API_KEY
)

# Tool-calling model
tool_llm = ChatGroq(
    model=TOOL_MODEL,
    temperature=0,
    api_key=GROQ_API_KEY,
)


def count_tokens_text(text: str) -> int:
    return chat_llm.get_num_tokens(text or "")

def _run_llm_to_text(llm, messages: list[BaseMessage]) -> str:
    ai = llm.invoke(messages)
    return getattr(ai, "content", "") or ""


def find_last_user_index(msgs: list[BaseMessage]) -> int:
    """
    Return the index of the last HumanMessage-like entry.
    Falls back to the last message if no explicit human role is found.
    """
    for i in range(len(msgs) - 1, -1, -1):
        m = msgs[i]
        role = getattr(m, "type", getattr(m, "role", "")).lower()
        if role in ("human", "user"):
            return i
    return max(len(msgs) - 1, 0)


def summarize_old_context(
    old_msgs: list[BaseMessage],
    existing_summary: str | None = None,
) -> str:
    """
    Use the LLM to compress all context BEFORE the last user message
    into a short summary string (<= MAX_SUMMARY_TOKENS tokens).
    """
    if not old_msgs and not existing_summary:
        return ""

    # Build a simple transcript of old messages + previous summary
    pieces: list[str] = []
    if existing_summary:
        pieces.append(f"Previous summary:\n{existing_summary}\n")

    for m in old_msgs:
        role = getattr(m, "type", getattr(m, "role", "message"))
        content = getattr(m, "content", "")
        pieces.append(f"{role.upper()}: {content}")

    transcript = "\n".join(pieces)

    # Optional: limit chars going into summarizer
    max_chars = 6000
    if len(transcript) > max_chars:
        transcript = transcript[-max_chars:]

    prompt = f"""
You are a conversation summarizer for a coding assistant.

Below is the older part of the conversation (plus any previous summary).
Compress it into a concise summary (maximum ~{MAX_SUMMARY_TOKENS} tokens) capturing ONLY:

- The user's long-term goals and tasks
- Important decisions or actions already taken
- GitHub configuration (PAT, active repo/branch) if mentioned
- Any persistent configuration (tokens, repos, buckets, paths, etc.)

Do NOT add new instructions, do NOT invent tool calls.
Return only the summary text, no headings or bullet labels.

----
{transcript}
----
"""

    summary_msg = HumanMessage(content=prompt)
    clean_summary = _run_llm_to_text(chat_llm, [summary_msg])

     

    # If the summary is still too long, re-summarize it once more
    if count_tokens_text(clean_summary) > MAX_SUMMARY_TOKENS:
        shrink_prompt = f"""
Shorten the following summary so it fits within about {MAX_SUMMARY_TOKENS} tokens,
preserving only the most important configuration and decisions.

TEXT:
{clean_summary}
"""
        clean_summary = _run_llm_to_text(chat_llm, [HumanMessage(content=shrink_prompt)])

    return clean_summary


def compress_last_user_message_if_needed(last_user: HumanMessage) -> HumanMessage:
    """
    If the last user message is longer than MAX_LAST_USER_TOKENS,
    compress it into a shorter, intent-preserving version.
    """
    content = getattr(last_user, "content", "") or ""
    if count_tokens_text(content) <= MAX_LAST_USER_TOKENS:
        return last_user

    prompt = f"""
The user wrote a very long request that may exceed the token limit.

Your task:
- Rewrite the user's message into a shorter version (about {MAX_LAST_USER_TOKENS} tokens or less).
- Preserve ALL technical requirements, constraints, and explicit instructions.
- Remove chit-chat, repetition, or unrelated text.
- Do NOT add any new requirements that were not present.

Return ONLY the rewritten user request, with no commentary.

ORIGINAL REQUEST:
----
{content}
----
"""
    ai_text = _run_llm_to_text(chat_llm, [HumanMessage(content=prompt)])
    
    return HumanMessage(content=ai_text)


from langchain_core.messages import trim_messages

# optional trimmer (nice but not required)
trimmer = trim_messages(
    max_tokens=2500,
    token_counter=chat_llm,
    strategy="last",
    include_system=True,
)

def chat_node(state: ChatState, config: RunnableConfig):
    """
    Main chat node.

    - All messages BEFORE the last user message are summarized.
    - We keep the last user message + any AI/tool messages AFTER it.
    - Tools are therefore driven only by the latest user request,
      but the LLM still sees previous tool results so it doesn't loop.
    """
    msgs = state.get("messages", []) or []
    user_plan = str(state.get("plan") or "free").lower()
    existing_summary = state.get("summary", "") or ""

    

    if not msgs:
        return {
            "messages": [AIMessage(content="Hi! What can I help you with?")],
            "summary": existing_summary,
            "plan": user_plan,
        }


    configurable = config.get("configurable") or {}
    thread_id_val = configurable.get("thread_id", "UNKNOWN")
    user_id_val = configurable.get("user_id", "UNKNOWN")
    # NEW: Inject GitHub token from FastAPI request into state
    github_token_val = configurable.get("github_token")

    if github_token_val:
        # put token into state so tools can use it
        state["personal_access_token"] = github_token_val

    print("Github Token is:---------------------------------",github_token_val)

    # ---------- system rules ----------
    plan_rules = f"""
You are Genie, an AI coding assistant.

The current user plan is: "{user_plan}".
The current user ID is: "{user_id_val}".
The current thread ID is: "{thread_id_val}".
The current GitHub token is: "{github_token_val}"

Tool-calling principles:
- Only call tools when the **latest user message** clearly indicates that a tool action is required.
- Prefer plain language answers when the user is only asking for explanations, concepts, or guidance.
- Do not assume long chains of actions; perform only what the user’s request reasonably implies.

Rules:
- Only call `generate_react_project_to_supabase` if the user EXPLICITLY asks to:
  - "generate a new project", "create a new React project", "build a starter project", etc.
- Do NOT call `generate_react_project_to_supabase` when the user:
  - only asks to list projects
  - only asks to read or create documentation
  - only asks conceptual questions about React/Tailwind/Vite.
- When calling `generate_react_project_to_supabase`, `generate_project_docs_impl`
  and `upload_supabase_project_to_github`,
  you MUST provide the current user ID (`{user_id_val}`) and thread ID (`{thread_id_val}`)
  as required arguments: `user_id` and `thread_id`.
- When calling tools related Github(get_username,) then provide the personal_access_token ('{github_token_val}') from state.

- If the plan is "free":
    - DO NOT call any tools that interact with GitHub or AWS in any way.
    - This includes ALL GitHub tools (repo/branch/file operations, uploads, actions, etc.)
      and ALL AWS/deployment/ML-training tools.
    - You may still:
        - Answer conceptually (explain how to do something in GitHub/AWS in plain text).
        - Use non-GitHub/AWS tools such as documentation or code generation.
    - When the user asks for ANY GitHub or AWS action (create repo, list repos, branches, files,
      upload project, deploy to AWS, etc.), DO NOT call tools. Instead, reply with a natural
      language message like:
      "GitHub automation and AWS deployment are only available for premium users.
       Please upgrade your plan to use this feature."

- If the plan is "premium":
    - You may call the available GitHub, AWS, and deployment tools when appropriate.

Conversation summary:
- Any conversation summary provided in system messages is **for context only**.
- Do NOT infer new tool requests from the summary.
- Always base tool usage decisions on the **most recent user message** plus any explicit instructions.

CRITICAL:
- The conversation SUMMARY (if any) is for context only.
- Tools should only be called when the **latest user message** clearly requests an action.

General:
- Never claim a GitHub or AWS action ran unless you actually executed the tool.
- If you are unsure whether a tool touches GitHub or AWS, assume it DOES and avoid it for free users.
    """.strip()

    system_msg = SystemMessage(content=plan_rules)

    # ---------- split history around last user ----------
    last_user_idx = find_last_user_index(msgs)

    # everything before last user → summarize
    old_msgs = msgs[:last_user_idx]
    # last user + any tool results / AI messages after it
    tail_msgs = msgs[last_user_idx:]

    new_summary = summarize_old_context(old_msgs, existing_summary)
    if not old_msgs and not existing_summary:
        new_summary = ""
    else:
        new_summary = summarize_old_context(old_msgs, existing_summary)

    # compress last user if needed, but keep tail structure
    last_user_msg = tail_msgs[0]
    if isinstance(last_user_msg, HumanMessage):
        compressed_last_user = compress_last_user_message_if_needed(last_user_msg)
    else:
        compressed_last_user = HumanMessage(content=getattr(last_user_msg, "content", ""))

    # rebuild tail: [compressed last user] + later AI/tool messages unchanged
    tail_msgs = [compressed_last_user] + tail_msgs[1:]

    summary_system_msg = None
    if new_summary:
        summary_system_msg = SystemMessage(
            content=(
                "Conversation summary (for context only; DO NOT infer tool requests from this):\n"
                f"{new_summary}"
            )
        )

    # ---------- final message list for LLM ----------
    history: list[BaseMessage] = [system_msg]
    if summary_system_msg:
        history.append(summary_system_msg)
    history.extend(tail_msgs)

    # optional trimming
    try:
        final_messages = trimmer.invoke(history)
    except Exception:
        final_messages = history

    # ---------- plan-aware tools ----------
    allowed_tools = get_tools_for_plan(user_plan)
    llm_with_tools = tool_llm.bind_tools(allowed_tools)

    # debug (optional)
    print("--- FINAL MESSAGES SENT TO LLM ---")
    for m in final_messages:
        content = getattr(m, "content", "")
        print(type(m).__name__, "LEN:", len(content))
        print(content[:5000])
        print("----------------------------------------")

    total_tokens = sum(count_tokens_text(getattr(m, "content", "")) for m in final_messages)
    print(f"final messages: {len(final_messages)} messages, total tokens: {total_tokens}")

    # ---------- invoke ----------
    response = llm_with_tools.invoke(final_messages)

    return {
        "messages": [response],
        "summary": new_summary,
        "plan": user_plan,
    }

