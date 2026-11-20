import { readProtocol } from "./sse.js";
export async function postTurnStream(api, sid, payload, onEvent, signal) {
  const epoch = api.epoch;
  const response = await api.raw("/sessions/" + encodeURIComponent(sid) + "/turns/stream",
    { method: "POST", json: payload, signal });
  try {
    const result = await readProtocol(response, event => {
      if (epoch !== api.epoch) throw new Error("身份已改变，丢弃旧流。");
      onEvent(event);
    }, payload.request_id);
    if (result.state?.session_id !== sid) throw new Error("结果会话ID不匹配。");
    return result.state;
  } catch (error) {
    if (error.status === 401 && epoch === api.epoch) api.expire();
    throw error;
  }
}
