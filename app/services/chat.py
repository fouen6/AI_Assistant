"""会话执行与缓存、图运行、消息同步、单轮聊天和流式提交；JSON/SSE共用同一次执行。"""
import hashlib
import json
import logging
from time import perf_counter
from contextlib import contextmanager
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from app.errors import AppError, ChatServiceError
from app.db import execution_guard
from app.core.llm import get_llm
from app.core.checkpoint import open_checkpointer
from app.core.streaming import PreviewObserver
from app.graph.workflow import build_agent
from app.services.evidence import present
from app.repositories.sessions import SessionRepository, json_messages, snapshot_to_record, save_record, read_business_session


class ChatOnlyModel:
    def __init__(self, model):
        self.model = model

    def invoke(self, messages):
        response = self.model.invoke(messages)
        if not isinstance(response, AIMessage):
            raise ValueError("无工具聊天模型应返回 AIMessage。")
        if response.tool_calls or response.invalid_tool_calls:
            raise ValueError("无工具聊天分支返回了工具请求，已阻止执行。")
        return response


def describe(snapshot, sid):
    values = snapshot.values
    history = values.get("messages", [])
    pending = [{"interrupt_id": p.id, "value": p.value} for p in snapshot.interrupts]
    last = history[-1] if history else None
    if pending:
        status = "awaiting_approval"
    elif not history:
        status = "empty"
    elif (snapshot.next or values.get("stop_reason") or not isinstance(last, AIMessage)
          or last.tool_calls or last.invalid_tool_calls
          or not isinstance(last.content, str) or not last.content.strip()):
        status = "incomplete"
    else:
        status = "done"
    messages = []
    latest_view = {"text": "", "answer_status": "pending", "evidence": [], "tools": []}
    for index, m in enumerate(history):
        item = {"id": m.id, "role": m.type, "content": m.content}
        if isinstance(m, AIMessage):
            item["tool_calls"] = m.tool_calls
            item["invalid_tool_calls"] = m.invalid_tool_calls
            if not m.tool_calls and not m.invalid_tool_calls:
                latest_view = present(history[:index + 1])
                item.update(display_content=latest_view["text"],
                            answer_status=latest_view["answer_status"], evidence=latest_view["evidence"])
        if isinstance(m, ToolMessage):
            item.update(tool_call_id=m.tool_call_id, tool_status=m.status, name=m.name,
                        metadata=m.additional_kwargs)
        messages.append(item)
    return {"session_id": str(sid), "status": status,
            "reply": latest_view["text"] if status == "done" else "",
            "answer_status": latest_view["answer_status"] if status == "done" else "pending",
            "evidence": latest_view["evidence"] if status == "done" else [],
            "tools": latest_view["tools"] if status == "done" else [],
            "checkpoint_id": snapshot.config.get("configurable", {}).get("checkpoint_id"),
            "model_calls": values.get("model_calls", 0),
            "send_count": values.get("send_count", 0),
            "stop_reason": values.get("stop_reason") or (
                "上轮没有可展示的有效最终回复，请检查消息与工具请求。" if status == "incomplete" else None),
            "pending": pending, "messages": messages}


class GraphEngine:
    def __init__(self, observer=None):
        self.observer = observer

    def runtime(self, mode):
        if mode == "agent":
            from app.services.agent import agent_runtime
            return agent_runtime()
        if mode == "chat":
            model = get_llm(128)
            if self.observer is not None:
                model = model.with_config({"callbacks": [self.observer]})
            return ChatOnlyModel(model), {}
        raise AppError(409, "mode_not_ready", "不支持的会话类型。")

    @contextmanager
    def graph(self, mode=None):
        with open_checkpointer() as saver:
            model, tools = self.runtime(mode) if mode else (None, {})
            yield build_agent(saver, model, tools)

    def config(self, session):
        return {"configurable": {"thread_id": session["internal_thread"]}}

    def read(self, session):
        with self.graph() as graph:
            return graph.get_state(self.config(session))

    def preflight(self, session):
        from app.graph.workflow import turn_block_reason
        from app.services.agent import unresolved_reason
        snapshot = self.read(session)
        reason = turn_block_reason(snapshot) or unresolved_reason(session["internal_thread"])
        if not reason and describe(snapshot, session["id"])["status"] == "incomplete":
            reason = "上轮未得到有效最终回复，请先只读核对，不追加新消息。"
        if reason:
            raise AppError(409, "turn_blocked", reason)

    def turn(self, session, text):
        from app.graph.workflow import make_turn_input
        from app.services.agent import register_pending
        config = self.config(session)
        with self.graph(session["mode"]) as graph:
            before = graph.get_state(config)
            update = make_turn_input(before, text)
            if session["mode"] == "chat" and not before.values.get("messages"):
                update["messages"][0] = SystemMessage(content=(
                    "你是中文助手，简短回答。当前是普通聊天，没有查询公司资料或执行操作的工具。"
                ))
            graph.invoke(update, config=config)
            snapshot = graph.get_state(config)
            if snapshot.interrupts:
                register_pending(snapshot, session["internal_thread"])
            sync_session(session["internal_thread"], snapshot)
            return describe(snapshot, session["id"])


def fingerprint(kind, payload):
    raw = json.dumps({"kind": kind, "payload": payload}, sort_keys=True,
                     ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class SessionService:
    def __init__(self, store, engine, guard=execution_guard):
        self.store, self.engine, self.guard = store, engine, guard

    def execute(self, user, sid, rid, kind, payload, preflight, action, *, admin=False):
        started = perf_counter()
        outcome = "failed"
        try:
            result = self._execute(user, sid, rid, kind, payload, preflight, action, admin=admin)
            outcome = result.get("status", "returned")
            return result
        finally:
            logging.getLogger("assistant.operation").info(
                "sid=%s rid=%s kind=%s outcome=%s elapsed_ms=%.1f", sid, rid, kind,
                outcome, (perf_counter() - started) * 1000)

    def _execute(self, user, sid, rid, kind, payload, preflight, action, *, admin=False):
        with self.guard():
            self.store.actor(user, admin=admin)
            session = self.store.any_session(sid) if admin else self.store.owned(user["id"], sid)
            digest = fingerprint(kind, payload)
            old = self.store.operation(sid, rid)
            if old:
                if old["fingerprint"] != digest or old["kind"] != kind:
                    raise AppError(409, "request_conflict", "相同request_id的内容不一致。")
                if old["status"] == "completed":
                    return old["result_json"]
                raise AppError(409, "execution_uncertain", "本次操作未确认，不自动重放。")
            if self.store.unfinished(sid):
                raise AppError(409, "execution_uncertain", "该会话有未确认操作，请先只读核对。")
            preflight(session)
            self.store.start(sid, rid, kind, digest)
            try:
                result = action(session)
                self.store.finish(sid, rid, result)
                return result
            except Exception as exc:
                try:
                    self.store.uncertain(sid, rid)
                except Exception:
                    logging.getLogger(__name__).exception("未能登记uncertain，running也会阻止重放")
                raise AppError(409, "execution_uncertain",
                    "执行或同步结果未确认，请查询本次操作与检查点，不自动重放。") from exc

    def submit(self, user, sid, rid, message):
        return self.execute(user, sid, rid, "turn", {"message": message},
                            self.engine.preflight, lambda s: self.engine.turn(s, message))


def sync_session(thread_id, snapshot):
    record = snapshot_to_record(thread_id, snapshot)
    replaced = save_record(record)
    # save_record 已经退出自己的事务；这里是新连接读回。
    saved = read_business_session(thread_id)
    fields = (
        "thread_id", "checkpoint_id", "message_count", "model_calls",
        "send_count", "pending_count",
    )
    if saved is None or any(saved[key] != record[key] for key in fields):
        raise ValueError("业务写入已返回，但新连接读回的会话信息不一致。")
    if json_messages(saved["messages"]) != record["messages"]:
        raise ValueError("业务写入已返回，但新连接读回的完整消息不一致。")
    print("会话业务副本同步完成，新连接核对通过。")
    print("来源检查点ID：", record["checkpoint_id"])
    print("替换旧消息数：", replaced)
    print("业务消息数／待审批数／累计发送数：",
          saved["message_count"], saved["pending_count"], saved["send_count"])
    print("同步时间：", saved["synced_at"])
    return saved


class ChatService:
    def __init__(self, model = None):
        self.model = model

    def reply(self, message: str)-> str:
        model = self.model if self.model is not None else get_llm(128)
        messages = [
            SystemMessage(content=(
                "你是中文助手，请简短回答当前问题。"
                "本接口没有历史、公司资料查询或外部操作工具。"
                "不要声称执行了发送通知、审批等操作。"
            )),
            HumanMessage(content=message),
        ]
        print("进入 ChatService.reply：请求一次单轮模型回复。")
        try:
            response = model.invoke(messages)
        except Exception as exc:
            raise ChatServiceError("本地模型请求失败。") from exc

        if not isinstance(response, AIMessage):
            raise ChatServiceError("模型没有返回 AIMessage。")
        if response.tool_calls or response.invalid_tool_calls:
            raise ChatServiceError("本接口不接受工具调用回复。")
        if not isinstance(response.content, str) or not response.content.strip():
            raise ChatServiceError("模型没有返回非空文本。")
        return response.content


def get_sessions():
    return SessionService(SessionRepository(), GraphEngine())


class StreamFacade:
    def precheck(self, user, sid):
        store = SessionRepository()
        store.actor(user)
        store.owned(user["id"], sid)

    def submit(self, user, sid, rid, message, emit):
        engine = GraphEngine(observer=PreviewObserver(emit))
        return SessionService(SessionRepository(), engine).submit(user, sid, rid, message)



def run_once(facade, channel, user, sid, rid, message):
    try:
        result = facade.submit(user, sid, rid, message, channel.emit)
    except AppError as exc:
        logging.getLogger(__name__).exception("流式业务请求未返回正常结果")
        channel.emit("error", {"http_status": exc.status, "code": exc.code, "message": exc.message})
        channel.emit("end", {"outcome": "error"})
    except Exception:
        logging.getLogger(__name__).exception("流式执行未确认")
        channel.emit("error", {"http_status": 500, "code": "stream_uncertain",
            "message": "执行结果未确认，请查询原请求ID，不自动重发。"})
        channel.emit("end", {"outcome": "error"})
    else:
        # submit返回前，原SessionService已经finish缓存；这里不再写任何历史。
        channel.emit("result", {"state": result})
        channel.emit("end", {"outcome": "result"})
