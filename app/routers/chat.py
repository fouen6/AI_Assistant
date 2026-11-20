"""健康、单轮聊天、持久会话、操作查询和真实SSE；统一调用会话服务。"""
import asyncio
import queue
import time
from typing import Annotated
from uuid import UUID, uuid4
import psycopg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from app.auth import current_user
from app.errors import AppError, ChatServiceError
from app.db import check_schema
from app.schemas.api import TextBody, TextReply, SessionCreate, TurnBody
from app.core.streaming import Channel, frame
from app.services.chat import ChatService, SessionService, get_sessions, describe, StreamFacade, run_once
router = APIRouter(tags=["聊天与会话"])
INSTANCE_ID = uuid4().hex


def get_chat_service():
    return ChatService()


@router.get("/health", tags=["健康"])
def health():
    return {"status": "ok", "service": "assistant-api", "build": "stage10-final",
            "instance_id": INSTANCE_ID}


@router.post("/chat", response_model=TextReply, tags=["单轮聊天"])
def chat(req: TextBody, user: Annotated[dict, Depends(current_user)],
         service: Annotated[ChatService, Depends(get_chat_service)]):
    try:
        return TextReply(reply=service.reply(req.message))
    except ChatServiceError as exc:
        raise AppError(503, "model_unavailable", "聊天模型暂时不可用。") from exc


@router.post("/sessions", status_code=201)
def create(req: SessionCreate, user: Annotated[dict, Depends(current_user)],
           service: Annotated[SessionService, Depends(get_sessions)]):
    return service.store.create(user["id"], req.mode)


@router.get("/sessions")
def list_mine(user: Annotated[dict, Depends(current_user)],
              service: Annotated[SessionService, Depends(get_sessions)]):
    return service.store.list_owned(user["id"])


@router.get("/sessions/{sid}")
def read(sid: UUID, user: Annotated[dict, Depends(current_user)],
         service: Annotated[SessionService, Depends(get_sessions)]):
    session = service.store.owned(user["id"], sid)
    result = describe(service.engine.read(session), sid)
    result["write_blocked"] = service.store.unfinished(sid)
    return result


@router.post("/sessions/{sid}/turns")
def turn(sid: UUID, req: TurnBody, user: Annotated[dict, Depends(current_user)],
         service: Annotated[SessionService, Depends(get_sessions)]):
    return service.submit(user, sid, req.request_id, req.message)


@router.get("/sessions/{sid}/operations/{rid}")
def operation(sid: UUID, rid: UUID, user: Annotated[dict, Depends(current_user)],
              service: Annotated[SessionService, Depends(get_sessions)]):
    service.store.owned(user["id"], sid)
    row = service.store.operation(sid, rid)
    if row is None:
        raise AppError(404, "operation_not_found", "操作记录不存在。")
    return row


def get_stream_facade():
    return StreamFacade()


def track_worker(app, task):
    # 保留强引用；浏览器断开后任务仍由服务端执行并保存结果。
    tasks = getattr(app.state, "study_stream_workers", None)
    if tasks is None:
        tasks = app.state.study_stream_workers = set()
    tasks.add(task)
    task.add_done_callback(tasks.discard)


@router.post("/sessions/{sid}/turns/stream")
async def stream_turn(sid: UUID, req: TurnBody, request: Request,
    user: Annotated[dict, Depends(current_user)],
    facade: Annotated[StreamFacade, Depends(get_stream_facade)]):
    # 同步数据库检查移到线程，且在响应头发出前完成认证/归属检查。
    await asyncio.to_thread(facade.precheck, user, sid)
    channel = Channel(req.request_id)

    async def events():
        # 事件生成器真正开始消费时才提交一次后台业务，避免在构造Response时抢先运行。
        worker = asyncio.create_task(asyncio.to_thread(run_once, facade, channel,
            user, sid, req.request_id, req.message))
        track_worker(request.app, worker)
        last_sent = time.monotonic()
        try:
            yield frame("start", {"request_id": str(req.request_id), "session_id": str(sid)})
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event, data = channel.items.get_nowait()
                except queue.Empty:
                    if time.monotonic() - last_sent >= 10:
                        yield ": heartbeat\n\n"
                        last_sent = time.monotonic()
                    await asyncio.sleep(0.05)
                    continue
                yield frame(event, data)
                last_sent = time.monotonic()
                if event == "end":
                    return
        finally:
            channel.close()
            # 不cancel worker，也不重新调用submit；停止接收不等于停止业务。
    return StreamingResponse(events(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/ready", tags=["健康"])
def ready():
    try:
        return check_schema()
    except psycopg.Error as exc:
        raise AppError(503, "database_not_ready", "数据库尚未就绪。") from exc
