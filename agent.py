import os
import atexit
from contextlib import ExitStack

# LangGraph imports
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.postgres import PostgresSaver

# Local imports
from utils.state import ChatState
from utils.nodes import chat_node
from utils.tools import ALL_TOOLS


# Create a memory object
DB_URI = os.getenv("SUPABASE_DB_URI")
_stack = ExitStack()
checkpointer = None
if DB_URI:
    try:
        print(f"[checkpoint] Trying PostgresSaver with DB_URI={DB_URI!r}")
        checkpointer = _stack.enter_context(PostgresSaver.from_conn_string(DB_URI))
        # safe to call; creates tables if missing
        checkpointer.setup()
        print("[checkpoint] Using PostgresSaver (Supabase)")
    except Exception as e:
        print(f"[checkpoint] WARNING: PostgresSaver init failed, falling back to MemorySaver: {e}")
# Make sure the connection closes cleanly when the process exits
atexit.register(_stack.close)

tools = ALL_TOOLS
tool_node = ToolNode(tools)

# -------------------
# 4. Graph
# -------------------
graph = StateGraph(ChatState)
graph.add_node("chat_node", chat_node)
graph.add_node("tools", tool_node)

graph.add_edge(START, "chat_node")

graph.add_conditional_edges("chat_node",tools_condition)
graph.add_edge('tools', 'chat_node')
chatbot = graph.compile(checkpointer=checkpointer)


def retrieve_all_threads(user_id: str) -> list[str]:
    """
    Return all thread_ids for a specific user_id.
    """
    all_threads: set[str] = set()

    # Filter by configurable.user_id
    for checkpoint in checkpointer.list(
        None, filter={"user_id": user_id}
    ):
        cfg = checkpoint.config.get("configurable", {})
        tid = cfg.get("thread_id")
        if tid:
            all_threads.add(str(tid))

    return list(all_threads)
