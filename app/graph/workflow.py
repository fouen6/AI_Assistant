"""模型→普通工具/人工审批→工具结果→模型；只读图不创建模型客户端。"""
from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage, AIMessage
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from app.config import MAX_MODEL_CALLS
from app.graph.state import AgentState
from app.graph.prompts import SYSTEM_PROMPT, KNOWLEDGE_RULES
from app.tools.notice import send_notice


def execute_tool_call(tool_call: dict, tool_map: dict)->ToolMessage:
    name = tool_call["name"]
    args = tool_call["args"]
    registry = tool_map

    try:
        selected_tool = registry.get(name)

        if selected_tool is None:
            raise ValueError(f"未注册的工具:{name}")

        result = selected_tool.invoke(args)
        content = str(result)
        status = "success"

    except Exception as exc :
        content = f"工具执行失败：{type(exc).__name__}:{exc}"
        status = "error"

    return ToolMessage(
        content=content,
        tool_call_id = tool_call["id"],
        name=name,
        status=status,
    )


def approval_node(state:AgentState)->dict:
    print("进入审批节点")

    approved = interrupt({
        "question":"是否批准这个操作？",
        "task":state["task"],
        "tool":send_notice.name,
        "args":{
            "recipient": state["recipient"],
            "content": state["content"],
        },
    })

    print("收到审批结果：",approved)
    return {"approved":approved}


def after_approval(state:AgentState)->str:
    if state["approved"] is True:
        return "send"
    if state["approved"] is False:
        return "reject"
    raise ValueError("审批恢复值必须为 True 或 False。")


def send_notice_node(state:AgentState)->dict:
    print("进入send")
    if state["approved"] is not True:
        raise ValueError("未经批准，不能执行模拟通知。")
    if state["tool_call_id"] is None:
        raise ValueError("缺少工具调用ID.")

    result = send_notice.invoke({
        "recipient":state["recipient"],
        "content": state["content"],
    })

    return {
        "send_count":state["send_count"]+1,
        "notice_result":result,
        "messages": [ToolMessage(
            content=result,
            name=send_notice.name,
            tool_call_id=state["tool_call_id"],
            status="success",
            additional_kwargs={"approval_decision": "approved", "simulated": True},
        )],
    }


def reject_node(state:AgentState)->dict:
    print("进入reject")
    if state["approved"] is not False:
        raise ValueError("当前状态不是已拒绝。")
    if state["tool_call_id"] is None:
        raise ValueError("缺少工具调用ID.")

    return {
        "messages":[ToolMessage(
            content=(
                "终端审批人拒绝执行本次操作，send_notice 未执行。"
                "程序没有获取收件人的意见，不能推断收件人是否愿意接收。"
            ),
            name=send_notice.name,
            tool_call_id=state["tool_call_id"],
            status="error",
            additional_kwargs={"approval_decision": "rejected", "simulated": True},
        )],
    }


def resume_approval(
        graph,
        config,
        interrupt_id:str,
        approved:bool,
):
    snapshot = graph.get_state(config)
    pending_ids = {
        item.id for item in snapshot.interrupts
    }

    if interrupt_id not in pending_ids:
        print("拒绝恢复：当前会话没有这条待审批请求。")
        return None

    return graph.invoke(
        Command(resume={interrupt_id:approved}),
        config=config,
    )


def build_agent(checkpointer, request_model=None, direct_tool_map=None):
    direct_tool_map = dict(direct_tool_map or {})
    direct_tool_names = frozenset(direct_tool_map)
    def ordinary_tools_node(state: AgentState) -> dict:
        call = state["pending_call"]
        if call is None or call["name"] not in direct_tool_map:
            raise ValueError("普通工具节点只允许执行本次注册的普通工具。")
        print("-----------进入tools：",call["name"],"参数：",call["args"],"-------------")
        tool_message = execute_tool_call(call, tool_map=direct_tool_map)
        return {"messages": [tool_message]}
    
    def request_node(state:AgentState)->dict:
        if request_model is None:
            raise ValueError("只读图没有模型客户端，不能执行模型节点。")

        update = {
            "pending_call":None,
            "tool_call_id": None,
            "recipient": "",
            "content": "",
            "approved": None,
            "notice_result": None,
            "stop_reason": None,
        }

        if state["model_calls"] >= MAX_MODEL_CALLS:
            update["stop_reason"] = "已经到达模型请求上限，未再次请求模型。"
            return update

        number = state["model_calls"] + 1
        print(f"---------进入request:第{number}次模型请求-------------")
        response = request_model.invoke(state["messages"])
        print("模型提出的工具请求:",response.tool_calls)
        update["messages"] = [response]
        update["model_calls"] = number

        if not response.tool_calls:
            return update

        if len(response.tool_calls) != 1:
            update["stop_reason"] = "本次执行每次只处理一条工具请求，当前批次未执行任何工具。"
            return update

        call = response.tool_calls[0]
        name = call["name"]
        if not call.get("id"):
            update["stop_reason"] = "工具请求缺少调用ID，未进入审批。"
            return update

        if name in direct_tool_names:
            update["pending_call"] = call
            return update

        if name != send_notice.name:
            update["stop_reason"] = f"不支持的工具:{name},未执行工具。"
            return update

        try:
            args = (
                send_notice.get_input_schema()
                .model_validate(call["args"])
                .model_dump()
            )
        except ValueError as exc:
            update["stop_reason"] = f"通知参数不合法，未进入审批:{exc}"
            return update

        update.update({
            "pending_call":{**call,"args":args},
            "recipient": args["recipient"],
            "content": args["content"],
            "tool_call_id": call["id"],
        })
        return update

    def after_request(state: AgentState) -> str:
        call = state["pending_call"]
        if call is None:
            return "end"
        if call["name"] in direct_tool_names:
            return "tools"
        if call["name"] == send_notice.name:
            return "approval"
        raise ValueError("待处理工具没有对应的执行路径。")

    def report_node(state:AgentState)->dict:
        tool_message = state["messages"][-1]
        if not isinstance(tool_message,ToolMessage):
            raise ValueError("report前应有一条工具处理结果.")

        if tool_message.name == "search_handbook":
            execution_text = (
                "手册检索已返回候选原文，是否包含答案仍需核对。"
                if tool_message.status == "success"
                else "手册检索失败。"
            )
        elif tool_message.name in direct_tool_names:
            execution_text = tool_message.content
        elif tool_message.name == send_notice.name:
            if state["approved"] is True:
                execution_text = state['notice_result']
            elif state["approved"] is False:
                execution_text = "审批人拒绝了本次操作，模拟通知未进行。"
            else:
                raise ValueError("通知处理结果缺少审批决定。")
        else:
            raise ValueError("无法展示未知工具的处理结果。")

        print("执行结论：",execution_text)
        print("工具处理状态：",tool_message.status)
        print("工具处理结果：",tool_message.content)
        print("对应工具调用ID：",tool_message.tool_call_id)
        if state["model_calls"] >= MAX_MODEL_CALLS:
            return {"stop_reason":(
                f"已达到 {MAX_MODEL_CALLS} 次模型请求上限。"
                "工具处理结果已记录，尚未生成最终回答。"
            )}

        return {}

    def after_report(state:AgentState)->str:
        return "end" if state["stop_reason"] else "request"


    builder = StateGraph(AgentState)
    builder.add_node("request", request_node)
    builder.add_node("approval",approval_node)
    builder.add_node("send",send_notice_node)
    builder.add_node("reject",reject_node)
    builder.add_node("report",report_node)
    builder.add_node("tools", ordinary_tools_node)

    builder.add_edge(START,"request")
    builder.add_conditional_edges(
        "request",
        after_request,
        {"tools": "tools", "approval": "approval", "end": END},
    )
    builder.add_conditional_edges("approval",after_approval,{"send":"send","reject":"reject"})
    for source in ("tools", "send", "reject"):
        builder.add_edge(source, "report")
    builder.add_conditional_edges("report",after_report,{"request":"request","end":END})

    return builder.compile(checkpointer=checkpointer)


def turn_block_reason(snapshot) -> str | None:
    if snapshot.interrupts:
        return "当前会话有待审批请求；当前实现先保留现场，不能追加新问题。"
    if snapshot.next:
        return "当前会话仍有未完成节点；先保留现场，当前实现不自动重试。"

    history = snapshot.values.get("messages", [])
    if history:
        last = history[-1]
        if not isinstance(last, AIMessage) or last.tool_calls:
            return "上一轮尚无最终 AI 回答，不能直接追加消息掩盖未完成的工具协议。"
    return None


def make_turn_input(snapshot, question: str) -> dict:
    reason = turn_block_reason(snapshot)
    if reason:
        raise ValueError(reason)
    question = question.strip()
    if not question:
        raise ValueError("新一轮问题不能为空。")

    old = snapshot.values
    messages = [HumanMessage(content=question)]
    if not old.get("messages"):
        messages.insert(0, SystemMessage(content=SYSTEM_PROMPT + KNOWLEDGE_RULES))

    return {
        "messages": messages,
        "task": "审批模型提出的模拟通知",
        "recipient": "",
        "content": "",
        "tool_call_id": None,
        "approved": None,
        "send_count": old.get("send_count", 0),
        "notice_result": None,
        "stop_reason": None,
        "pending_call": None,
        "model_calls": 0,
    }
