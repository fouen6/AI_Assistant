"""Agent工具绑定、审批请求核对、决定与恢复；审批SQL位于repositories/approvals.py。"""
from functools import lru_cache
from langchain_core.messages import AIMessage, ToolMessage
from app.errors import AppError
from app.core.llm import get_llm
from app.graph.workflow import resume_approval
from app.repositories.approvals import register_request, decide_request, record_result, read_records
from app.services.evidence import EvidenceModel
from app.tools.calculator import multiply, add
from app.tools.notice import send_notice
from app.tools.handbook import make_api_handbook_tool


@lru_cache(maxsize=1)
def agent_runtime():
    ordinary = [multiply, add, make_api_handbook_tool()]
    bound = get_llm(1024).bind_tools([*ordinary, send_notice])
    return EvidenceModel(bound), {tool.name: tool for tool in ordinary}



def pending_request(snapshot, interrupt_id=None):
    pending = snapshot.interrupts
    if interrupt_id is not None and interrupt_id not in {p.id for p in pending}:
        raise ValueError("当前会话没有这条待审批请求。")
    if not pending:
        return None
    if len(pending) != 1:
        raise ValueError("本课每次只处理一条待审批请求。")

    item = pending[0]
    values = snapshot.values
    call = values.get("pending_call")
    args = {"recipient": values.get("recipient"), "content": values.get("content")}
    if not all(isinstance(v, str) and v.strip() for v in args.values()):
        raise ValueError("待审批通知的参数不完整。")
    if not isinstance(call, dict) or call.get("name") != "send_notice":
        raise ValueError("待审批请求必须是 send_notice。")
    call_id = call.get("id")
    if not isinstance(call_id, str) or not call_id.strip():
        raise ValueError("工具调用 ID 缺失。")
    if call_id != values.get("tool_call_id") or call.get("args") != args:
        raise ValueError("图状态与待执行工具请求不一致。")
    if values.get("approved") is not None:
        raise ValueError("暂停时的临时审批字段应为 None。")
    payload = item.value
    if (not isinstance(payload, dict) or payload.get("tool") != "send_notice"
            or payload.get("args") != args):
        raise ValueError("中断展示内容与工具参数不一致。")

    history = values.get("messages", [])
    last = history[-1] if history else None
    if (not isinstance(last, AIMessage) or len(last.tool_calls) != 1
            or any(last.tool_calls[0].get(k) != v for k, v in {
                "id": call_id, "name": "send_notice", "args": args,
            }.items())):
        raise ValueError("没有找到对应的最新 AI 工具请求。")
    if any(isinstance(m, ToolMessage) and m.tool_call_id == call_id for m in history):
        raise ValueError("该工具调用 ID 已有结果，不能当作新请求登记。")
    return {
        "interrupt_id": item.id, "tool_call_id": call_id,
        "tool_name": "send_notice", "args": args,
    }


def register_pending(snapshot, thread_id, interrupt_id=None):
    request = pending_request(snapshot, interrupt_id)
    if request is not None:
        created = register_request(thread_id, **request, origin="agent")
        print("审批请求登记：", "新增" if created else "已存在，参数核对一致")
    return request


def save_approved_outcome(snapshot, thread_id, request):
    matches = [
        m for m in snapshot.values.get("messages", [])
        if isinstance(m, ToolMessage) and m.name == request["tool_name"]
        and m.tool_call_id == request["tool_call_id"]
    ]
    status = "unknown"
    text = "未从检查点中确认本次工具结果，不自动重试通知。"
    if len(matches) == 1:
        message = matches[0]
        if isinstance(message.content, str) and message.content.strip():
            if message.status in {"success", "error"}:
                status = "succeeded" if message.status == "success" else "failed"
                text = message.content
    record_result(thread_id, request["interrupt_id"], status, text)
    print("已登记工具执行结果：", status)


def unresolved_reason(thread_id):
    for row in read_records(thread_id):
        if (row["origin"] == "agent" and row["decision_status"] == "approved"
                and row["execution_status"] in {"not_started", "unknown"}):
            return (
                f"中断 {row['interrupt_id']} 已批准但结果未确认；"
                "先用 read 和 records 核对，本课不自动重试或追加聊天。"
            )
    return None


def resume_with_record(graph, config, thread_id, interrupt_id, approved, actor):
    request = register_pending(graph.get_state(config), thread_id, interrupt_id)
    if not decide_request(thread_id, interrupt_id, approved, actor):
        print("拒绝恢复：该请求已经作出决定，本次不执行图。")
        return None

    print("审批决定已单独提交：", "approved" if approved else "rejected")
    try:
        result = resume_approval(graph, config, interrupt_id, approved)
        if result is None:
            raise RuntimeError("决定已登记，但图未接受这条恢复请求。")
    except Exception:
        print("图恢复发生异常；不能仅凭异常断定通知没有执行。")
        if approved:
            try:
                save_approved_outcome(graph.get_state(config), thread_id, request)
            except Exception as record_error:
                print("结果登记也未完成：", type(record_error).__name__)
                print("请读回两类状态核对，不要重放通知。")
        raise

    # 图成功返回与业务结果写入是两个边界，不能混称一个事务。
    if approved:
        try:
            save_approved_outcome(graph.get_state(config), thread_id, request)
        except Exception as exc:
            raise RuntimeError(
                "图恢复已返回，但业务结果登记未完成；只读核对，不要重放通知。"
            ) from exc
    else:
        print("审批被拒绝；执行状态保持 not_started，不登记工具失败。")
    return result


def check_pending(snapshot, rows, interrupt_id):
    try:
        request = pending_request(snapshot, interrupt_id)
    except ValueError as exc:
        raise AppError(409, "approval_mismatch", "当前会话没有对应的有效待审批请求。") from exc
    if request is None:
        raise AppError(409, "approval_mismatch", "当前会话没有待审批请求。")
    row = next((r for r in rows if r["interrupt_id"] == interrupt_id), None)
    if (row is None or row["origin"] != "agent"
            or row["tool_name"] != request["tool_name"]
            or row["tool_call_id"] != request["tool_call_id"]
            or row["args_json"] != request["args"]):
        raise AppError(409, "approval_mismatch", "审批业务记录与当前图请求不一致。")
    if row["decision_status"] != "pending":
        raise AppError(409, "already_decided", "这条请求已经作出决定，不再次执行。")
    return request


class ApprovalService:
    def __init__(self, sessions):
        self.sessions = sessions

    def resume(self, user, sid, rid, interrupt_id, approved):
        from app.services.chat import sync_session, describe
        engine = self.sessions.engine

        def preflight(session):
            if session["mode"] != "agent":
                raise AppError(409, "wrong_mode", "普通聊天会话不能提交工具审批。")
            check_pending(engine.read(session), read_records(session["internal_thread"]),
                          interrupt_id)

        def action(session):
            config = engine.config(session)
            with engine.graph(session["mode"]) as graph:
                result = resume_with_record(graph, config, session["internal_thread"],
                                            interrupt_id, approved, actor=user["username"])
                if result is None:
                    raise RuntimeError("审批恢复未返回确定结果。")
                snapshot = graph.get_state(config)
                if snapshot.interrupts:
                    register_pending(snapshot, session["internal_thread"])
                sync_session(session["internal_thread"], snapshot)
                return describe(snapshot, sid)

        return self.sessions.execute(
            user, sid, rid, "resume", {"interrupt_id": interrupt_id, "approved": approved},
            preflight, action, admin=True,
        )
