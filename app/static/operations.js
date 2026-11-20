// 9.3：sessionStorage只保留查询所需的标识，不保存密码、Token、正文或历史。
export function key(owner, sid) { return "study9.pending." + owner + "." + sid; }
export function remember(owner, sid, request_id, kind) {
  const meta = { owner: String(owner), sid, request_id, kind };
  sessionStorage.setItem(key(owner, sid), JSON.stringify(meta));
  return meta;
}
export function pending(owner, sid) {
  try {
    const item = JSON.parse(sessionStorage.getItem(key(owner, sid)));
    return item && item.owner === String(owner) && item.sid === sid ? item : null;
  } catch (_) { return null; }
}
export function forget(owner, sid) { sessionStorage.removeItem(key(owner, sid)); }
export function allPending(owner) {
  const found = [];
  for (let i = 0; i < sessionStorage.length; i++) {
    const name = sessionStorage.key(i);
    if (!name.startsWith("study9.pending." + owner + ".")) continue;
    try {
      const item = JSON.parse(sessionStorage.getItem(name));
      if (item.owner === String(owner)) found.push(item);
    } catch (_) {}
  }
  return found;
}
export function clearPending() {
  for (let i = sessionStorage.length - 1; i >= 0; i--) {
    const name = sessionStorage.key(i);
    if (name.startsWith("study9.pending.")) sessionStorage.removeItem(name);
  }
}
export function newPayload(message) {
  if (!message.trim()) throw new Error("问题不能为空。");
  return { request_id: crypto.randomUUID(), message };
}
