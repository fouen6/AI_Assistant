"""SSE帧、可关闭通道与增量回调；不执行会话业务。"""
import json
import queue
import threading
from langchain_core.callbacks import BaseCallbackHandler

def frame(event, data):
    # JSON转义正文中的换行；SSE自身以空行结束。
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False)
    return f"event: {event}\ndata: {payload}\n\n"

class Channel:
    """只限制可丢弃的草稿预览；最终状态仍由服务/数据库负责。"""
    def __init__(self, request_id, max_previews=256):
        self.request_id = str(request_id)
        self.items = queue.SimpleQueue()
        self.closed = threading.Event()
        self.max_previews = max_previews
        self.previews = 0
        self.notified = False

    def emit(self, event, data):
        if self.closed.is_set():
            return
        if event in {"delta", "draft_start"}:
            if self.previews >= self.max_previews:
                if not self.notified:
                    self.notified = True
                    self.items.put(("preview_limit", {"request_id": self.request_id}))
                return
            self.previews += 1
        self.items.put((event, {**data, "request_id": self.request_id}))

    def close(self):
        self.closed.set()

class PreviewObserver(BaseCallbackHandler):
    # 回调异常不能暗中改变业务流程；这里只处理字符串，不接收工具参数。
    def __init__(self, emit):
        self.emit = emit
        self.parts = []
        self.size = 0

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self.parts = []; self.size = 0
        self.emit("draft_start", {"run_id": str(run_id)})

    def on_llm_new_token(self, token, **kwargs):
        if not isinstance(token, str) or not token:
            return
        self.parts.append(token); self.size += len(token)
        if self.size >= 24:
            self.flush()

    def flush(self):
        if self.parts:
            self.emit("delta", {"text": "".join(self.parts)})
            self.parts = []; self.size = 0

    def on_llm_end(self, response, **kwargs):
        self.flush()

    def on_llm_error(self, error, **kwargs):
        # 不把异常/未完成正文拼成可确认的最终答案。
        self.parts = []; self.size = 0
