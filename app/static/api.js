// 9.2：只有这个模块负责 HTTP 和身份凭证；Token 不写入浏览器存储。
export class ApiError extends Error {
  constructor(status, code, message, fields = []) {
    super(message); this.status = status; this.code = code; this.fields = fields;
  }
}
export class ApiClient {
  constructor(onUnauthorized = () => {}) {
    this.token = ""; this.epoch = 0; this.onUnauthorized = onUnauthorized;
  }
  clear() { this.token = ""; this.epoch += 1; }
  expire() { this.clear(); this.onUnauthorized(); }
  async raw(path, { method = "GET", json, body, auth = true, signal } = {}) {
    const epoch = this.epoch;
    const headers = {};
    if (auth) {
      if (!this.token) throw new ApiError(401, "login_required", "请先登录。");
      headers.Authorization = "Bearer " + this.token;
    }
    if (json !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(json);
    }
    let response;
    try { response = await fetch(path, { method, headers, body, signal }); }
    catch (error) {
      if (error.name === "AbortError") throw error;
      throw new ApiError(0, "network_error", "无法确认服务器是否收到请求，请先查询操作记录。");
    }
    if (epoch !== this.epoch) throw new ApiError(0, "stale_response", "身份已变化，忽略旧响应。");
    if (!response.ok) {
      let payload = {};
      try { payload = await response.json(); } catch (_) {}
      if (epoch !== this.epoch) throw new ApiError(0, "stale_response", "身份已变化，忽略旧响应。");
      if (response.status === 401 && auth) this.expire();
      const error = payload.error || {};
      throw new ApiError(response.status, error.code || "http_error",
        error.message || "请求未成功。", error.fields || []);
    }
    return response;
  }
  async call(path, options) {
    const epoch = this.epoch;
    const response = await this.raw(path, options);
    const data = response.status === 204 ? null : await response.json();
    if (epoch !== this.epoch) throw new ApiError(0, "stale_response", "身份已变化，忽略旧响应。");
    return data;
  }
  async login(username, password) {
    this.clear();
    const data = await this.call("/auth/login", {
      method: "POST", auth: false, json: { username, password }
    });
    this.token = data.access_token;
    return this.call("/auth/me");
  }
  register(username, password) {
    return this.call("/auth/register", { method: "POST", auth: false, json: { username, password } });
  }
  me() { return this.call("/auth/me"); }
  sessions() { return this.call("/sessions"); }
  createSession(mode) { return this.call("/sessions", { method: "POST", json: { mode } }); }
  snapshot(sid, admin = false) {
    return this.call((admin ? "/admin" : "") + "/sessions/" + encodeURIComponent(sid));
  }
  operation(sid, rid, admin = false) {
    return this.call((admin ? "/admin" : "") + "/sessions/" + encodeURIComponent(sid) +
      "/operations/" + encodeURIComponent(rid));
  }
  turn(sid, payload) {
    return this.call("/sessions/" + encodeURIComponent(sid) + "/turns", { method: "POST", json: payload });
  }
  approvals(decision = "pending") {
    const query = new URLSearchParams({ limit: "50" });
    if (decision) query.set("decision", decision);
    return this.call("/admin/approvals?" + query);
  }
  resume(sid, iid, payload) {
    return this.call("/admin/sessions/" + encodeURIComponent(sid) +
      "/approvals/" + encodeURIComponent(iid), { method: "POST", json: payload });
  }
  documents() { return this.call("/admin/documents"); }
  upload(file) {
    const form = new FormData(); form.append("file", file);
    return this.call("/admin/documents", { method: "POST", body: form });
  }
  removeDocument(id) {
    return this.call("/admin/documents/" + encodeURIComponent(id), { method: "DELETE" });
  }
  users() { return this.call("/admin/users"); }
  changeUser(id, role, is_active) {
    return this.call("/admin/users/" + encodeURIComponent(id),
      { method: "PATCH", json: { role, is_active } });
  }
  profile() { return this.call("/profile"); }
  avatar(avatar_key) {
    return this.call("/profile/avatar", { method: "PATCH", json: { avatar_key } });
  }
}
export function explain(error) {
  if (error.code === "stale_response") return "";
  const prefix = error.status ? String(error.status) + " / " : "";
  const details = (error.fields || []).map(f => (f.loc || []).join(".") + "：" + f.msg).join("\n");
  return prefix + (error.code || error.name) + "：" + error.message + (details ? "\n" + details : "");
}
