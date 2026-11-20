"""节点间共享的Agent状态；消息使用add_messages合并。"""
from typing import Annotated, TypedDict
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    task: str
    recipient: str
    content: str
    tool_call_id: str | None
    approved: bool | None
    send_count: int
    notice_result: str | None
    stop_reason: str | None
    pending_call: dict | None
    model_calls: int
