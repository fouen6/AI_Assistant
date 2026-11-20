// 9.6：读取字节流不等于读取完整事件。先保留半行，再按空行分帧。
export class SSEParser {
  constructor(onEvent, maxChars = 128000) {
    this.onEvent = onEvent; this.maxChars = maxChars; this.buffer = "";
    this.event = "message"; this.data = []; this.size = 0;
  }
  feed(text, final = false) {
    this.buffer += text;
    for (;;) {
      const m = /[\r\n]/.exec(this.buffer);
      if (!m) break;
      const i = m.index, ch = this.buffer[i];
      if (!final && ch === "\r" && i === this.buffer.length - 1) break;
      const width = ch === "\r" && this.buffer[i + 1] === "\n" ? 2 : 1;
      const line = this.buffer.slice(0, i);
      this.buffer = this.buffer.slice(i + width);
      if (line.length + this.size > this.maxChars) throw new Error("单条SSE事件超过课程上限。");
      this.line(line);
    }
    // 没有空行收尾的半个事件不派发；业务结束必须另见end事件。
    if (this.buffer.length + this.size > this.maxChars) throw new Error("单条SSE事件超过课程上限。");
    if (final) this.buffer = "";
  }
  line(line) {
    if (!line) {
      if (this.data.length) this.onEvent({ event: this.event, text: this.data.join("\n") });
      this.event = "message"; this.data = []; this.size = 0; return;
    }
    if (line.startsWith(":")) return;
    const at = line.indexOf(":");
    const name = at < 0 ? line : line.slice(0, at);
    let value = at < 0 ? "" : line.slice(at + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (name === "event") this.event = value || "message";
    if (name === "data") { this.data.push(value); this.size += value.length; }
  }
}
export async function readProtocol(response, onEvent, expectedRequest = null) {
  if (!response.headers.get("content-type")?.toLowerCase().startsWith("text/event-stream")) {
    throw new Error("服务器没有返回text/event-stream。");
  }
  if (!response.body) throw new Error("响应没有可读正文。");
  let started = false, ended = false, result = null, failure = null;
  const parser = new SSEParser(frame => {
    if (ended) throw new Error("end之后收到额外事件。");
    let data;
    try { data = JSON.parse(frame.text); } catch (_) { throw new Error("SSE data不是合法JSON。"); }
    if (!data || typeof data !== "object" || Array.isArray(data)) throw new Error("SSE data必须为JSON对象。");
    if (expectedRequest && data.request_id !== expectedRequest) throw new Error("事件请求ID不匹配。");
    if (frame.event === "start") {
      if (started) throw new Error("重复start。"); started = true;
    } else {
      if (!started) throw new Error("缺少start。");
      if (frame.event === "result") {
        if (result || failure) throw new Error("重复或冲突的结果。"); result = data;
      } else if (frame.event === "error") {
        if (failure || result) throw new Error("重复或冲突的错误。"); failure = data;
      } else if (frame.event === "end") {
        if (!["result", "error"].includes(data.outcome) || (!result && !failure) || (data.outcome === "result") !== !!result) {
          throw new Error("end与实际结果不一致。");
        }
        ended = true;
      } else if (["draft_start", "delta", "preview_limit"].includes(frame.event)) {
        if (result || failure) throw new Error("结果后仍发送草稿。");
      } else { throw new Error("未知事件类型：" + frame.event); }
    }
    onEvent({ event: frame.event, data });
  });
  const reader = response.body.getReader(), decoder = new TextDecoder("utf-8", { fatal: true });
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) { parser.feed(decoder.decode(), true); break; }
      parser.feed(decoder.decode(value, { stream: true }));
    }
  } finally { reader.releaseLock(); }
  if (!ended) throw new Error("连接结束但没有有效end：结果尚未确认。");
  if (failure) {
    const error = new Error(failure.message || "流式执行失败。");
    error.name = "StreamBusinessError"; error.status = failure.http_status || 0; error.code = failure.code;
    throw error;
  }
  return result;
}
