from typing import TypedDict, Annotated
from langgraph.graph.message import add_messages

# We need the BaseMessage definition if using LangChain messages
try:
    from langchain_core.messages import BaseMessage
except ImportError:
    # Fallback definition if import fails
    class BaseMessage: pass

class ChatState(TypedDict):
    """Represents the state of the conversation and context."""
    messages: Annotated[list[BaseMessage], add_messages]
    summary: str
    plan: str

# GitHub State
class GitHubState(TypedDict):
    """Represents the active configuration for GitHub operations."""
    active_repo: Annotated[str, "Active Repositry"]
    active_branch: Annotated[str, "Active branch of repositry"]

