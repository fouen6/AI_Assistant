"""真实本机HTTP核心验收：run创建本次数据，readback只读复核原结果。"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from uuid import uuid4
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


class CheckFailed(RuntimeError):
    pass


def check(condition, message):
    if not condition:
        raise CheckFailed(message)


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def signature(state):
    return {k: state[k] for k in ["session_id", "checkpoint_id", "status", "send_count", "messages"]}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, base):
        parts = urlsplit(base)
        check(parts.scheme == "http" and parts.hostname in {"127.0.0.1", "localhost"}
              and not parts.username and not parts.query and not parts.fragment
              and parts.path in {"", "/"}, "验收地址仅允许本机HTTP根地址。")
        self.base, self.tokens = base.rstrip("/"), {}
        self.opener = build_opener(NoRedirect())

    def open(self, method, path, who=None, body=None, content_type=None):
        headers = {"Accept": "application/json"}
        if who:
            headers["Authorization"] = "Bearer " + self.tokens[who]
        if isinstance(body, dict):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            content_type = "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        try:
            return self.opener.open(Request(self.base + path, data=body, headers=headers, method=method), timeout=180)
        except HTTPError as exc:
            return exc
        except (URLError, TimeoutError, OSError) as exc:
            raise CheckFailed("网络结果未确认；查询报告里的原request_id，不自动重发。") from exc

    def call(self, method, path, who=None, body=None, expected=200, content_type=None):
        with self.open(method, path, who, body, content_type) as response:
            raw = response.read()
            data = json.loads(raw) if raw else None
            check(response.status == expected,
                  f"{method} {path}: 预期{expected}，实际{response.status}；{data}")
            return data

    def login(self, key, username, password):
        data = self.call("POST", "/auth/login", body={"username": username, "password": password})
        self.tokens[key] = data["access_token"]  # 不写文件，不打印，不保存登录响应。
        return self.call("GET", "/auth/me", key)

    def upload(self, filename, raw):
        boundary = "core-" + uuid4().hex
        part = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                'Content-Type: text/plain; charset=utf-8\r\n\r\n').encode("utf-8")
        body = part + raw + f"\r\n--{boundary}--\r\n".encode("ascii")
        return self.call("POST", "/admin/documents", "admin", body, expected=201,
                         content_type="multipart/form-data; boundary=" + boundary)

    def stream(self, sid, body):
        events, event, lines = [], "message", []
        self.last_events = events
        started = time.perf_counter()
        with self.open("POST", f"/sessions/{sid}/turns/stream", "employee", body) as response:
            check(response.status == 200, f"流式接口HTTP{response.status}，未通过。")
            check(response.headers.get_content_type() == "text/event-stream", "响应不是SSE。")
            for raw in response:
                line = raw.decode("utf-8").rstrip("\r\n")
                if line.startswith(":"):
                    continue
                if not line:
                    if lines:
                        data = json.loads("\n".join(lines))
                        check(data.get("request_id") == body["request_id"], "SSE请求ID不一致。")
                        events.append({"event": event, "data": data,
                                       "elapsed_ms": round((time.perf_counter() - started) * 1000, 1)})
                    event, lines = "message", []
                elif line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    lines.append(line[5:].lstrip(" "))
        check(bool(events) and events[0]["event"] == "start", "缺少start。")
        check(events[-1]["event"] == "end" and events[-1]["data"].get("outcome") == "result", "流未正常结束。")
        check(not any(e["event"] == "error" for e in events), "流中出现错误。")
        results = [e for e in events if e["event"] == "result"]
        check(len(results) == 1, "result数量不是1。")
        return results[0]["data"]["state"], events


class CoreRun:
    def __init__(self, client, directory, config):
        self.client, self.directory, self.config = client, directory, config
        self.data = {"kind": "real_local_acceptance", "run_id": directory.name,
                     "base_url": client.base, "groups": [], "evidence": {},
                     "sessions": {}, "operations": [], "readback": "pending",
                     "ui": "由学习者另行报告页面观察，脚本不自动判断", "skipped": ["9.5 U1–U6及相关旧Token角色变更专项，按学习者要求不追补"]}

    def persist(self):
        save(self.directory / "results.json", self.data)
        lines = ["# 第十部分核心验收报告", "", f"运行：{self.data['run_id']}",
                 "范围：" + "、".join(self.data.get("requested_groups", ["G1", "G2", "G3", "G4", "G5"])), "",
                 "| 核心功能 | 结果 | 说明 |", "|---|---|---|"]
        for g in self.data["groups"]:
            lines.append(f"| {g['name']} | {g['status']} | {g['detail'].replace('|', '/').replace(chr(10), ' ')} |")
        lines += ["", f"重启后只读复核：{self.data['readback']}", f"页面观察：{self.data['ui']}", "",
                  "PASS仅表示这些固定样例通过，不代表任意问题正确或生产可用。",
                  "若有FAIL保留本目录，先看results.json中对应证据和原request_id，不自动换ID重发。",
                  "真实模型运行数据在results.json；未保存密码、Token或密码哈希。"]
        (self.directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def note(self, name, value):
        self.data["evidence"][name] = value
        self.persist()
        return value

    def group(self, name, action):
        started = time.perf_counter()
        try:
            detail = action()
            status = "PASS"
        except Exception as exc:
            status, detail = "FAIL", f"{type(exc).__name__}: {exc}"
        self.data["groups"].append({"name": name, "status": status, "detail": str(detail),
                                    "elapsed_s": round(time.perf_counter() - started, 2)})
        self.persist()
        print(f"{name} {status}：{detail}")

    def session(self, label, mode):
        sid = self.client.call("POST", "/sessions", "employee", {"mode": mode}, expected=201)["id"]
        self.data["sessions"][label] = {"sid": sid}
        self.persist()
        return sid

    def state(self, label, sid):
        state = self.client.call("GET", f"/sessions/{sid}", "employee")
        self.note(label, state)
        return state

    def operation(self, sid, kind, body, iid=None):
        self.data["operations"].append({"sid": sid, "kind": kind, "body": body, "interrupt_id": iid})
        self.persist()  # 先落ID，再发请求；网络异常后仍能查原操作。

    def turn(self, label, sid, text, stream=False):
        body = {"request_id": str(uuid4()), "message": text}
        self.operation(sid, "turn", body)
        if stream:
            try:
                result, events = self.client.stream(sid, body)
            finally:
                self.note(label + "_events", getattr(self.client, "last_events", []))
        else:
            result = self.client.call("POST", f"/sessions/{sid}/turns", "employee", body)
        self.note(label, result)
        return result, body

    def identity(self):
        c = self.client
        health = c.call("GET", "/health")
        check(health.get("build") == "stage10-final", "当前端口不是第十部分正式代码，请停止旧服务后重新启动。")
        self.note("health", health)
        c.call("GET", "/ready")
        c.call("GET", "/auth/me", expected=401)
        employee = c.login("employee", self.config["EMPLOYEE_USERNAME"], self.config["EMPLOYEE_PASSWORD"])
        admin = c.login("admin", self.config["ADMIN_USERNAME"], self.config["ADMIN_PASSWORD"])
        self.note("identity", {"employee": employee, "admin": admin})
        check(employee["role"] == "employee" and admin["role"] == "admin", "测试账号的实际角色不符合配置。")
        c.call("GET", "/admin/users", "employee", expected=403)
        old = c.call("GET", "/profile", "employee")
        other = c.call("GET", "/profile", "admin")
        new = "cat" if old["avatar_key"] != "cat" else "plant"
        try:
            c.call("PATCH", "/profile/avatar", "employee", {"avatar_key": new})
            check(c.call("GET", "/profile", "employee")["avatar_key"] == new, "头像未持久读回。")
            check(c.call("GET", "/profile", "admin") == other, "头像修改影响了其他账号。")
            check(c.call("GET", "/auth/me", "employee")["auth_version"] == employee["auth_version"], "头像不应撤销登录。")
        finally:
            c.call("PATCH", "/profile/avatar", "employee", {"avatar_key": old["avatar_key"]})
        self.note("avatar", {"saved_key": new, "restored": c.call("GET", "/profile", "employee")})
        return "登录、401/403、头像保存/隔离通过，头像已恢复原值。"

    def chat(self):
        sid = self.session("chat", "chat")
        phrase = "青柠" + self.directory.name[-6:]
        first, r1 = self.turn("chat_first", sid, f"请记住，本次对话的暗号是{phrase}。")
        check(first["status"] == "done" and len(first["messages"]) == 3, "首轮未正常保存。")
        second, r2 = self.turn("chat_second", sid,
            "先准确复述刚才的暗号，再用三句话介绍Python生成器的用途。", stream=True)
        events = self.data["evidence"]["chat_second_events"]
        result_at = next(i for i, e in enumerate(events) if e["event"] == "result")
        check(any(e["event"] == "delta" and e["data"].get("text") for e in events[:result_at]), "没有在result前观察到真实非空增量。")
        check(phrase in second["reply"] and len(second["messages"]) == 5, "多轮记忆或历史数量错误。")
        check(second["status"] == "done", "第二轮未完成。")
        cached = self.client.call("POST", f"/sessions/{sid}/turns", "employee", r1)
        check(cached == first, "旧请求没有返回原缓存。")
        current = self.state("chat_current", sid)
        check(signature(current) == signature(second), "重放旧请求改变了当前历史。")
        self.data["sessions"]["chat"]["signature"] = signature(current)
        return "JSON首轮→真实SSE第二轮；历史3→5；原ID缓存与当前历史分别正确。"

    def retrieval_meta(self, state):
        traces = state["tools"]
        search = next(t for t in traces if t["name"] == "search_handbook")
        payload = json.loads(search["result"])
        return payload

    def handbook(self):
        c = self.client
        raw = (ROOT / "data" / "员工手册.txt").read_bytes()
        text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        docs = c.call("GET", "/admin/documents", "admin")
        row = next((d for d in docs if d["filename"] == "员工手册.txt"), None)
        if row is None:
            row = c.upload("员工手册.txt", raw)
            self.note("handbook_seed", {"id": row["id"], "created": True, "retained": True})
        check(row["content_fingerprint"] == hashlib.sha256(text.encode("utf-8")).hexdigest(),
              "同名手册内容与本次固定题不一致；未覆盖现有文档，请人工核对。")
        sid = self.session("rag", "agent")
        result, _ = self.turn("rag", sid,
            "请严格分步：先只调用search_handbook查询员工手册住房补贴月标准和申请条件；"
            "收到原文后再调用multiply，a为查到的每月金额，b为12；收到结果后按证据契约选择完整规则原句。"
            "假设入职条件满足，12个月均获批并足额发放。")
        check(result["status"] == "done" and result["answer_status"] == "evidence_checked", "制度答复没有通过证据检查。")
        ts = result["tools"]
        check([t["name"] for t in ts] == ["search_handbook", "multiply"] and all(t["status"] == "success" for t in ts), "没有按序成功检索再计算。")
        check(float(ts[1]["args"]["a"]) == 800 and float(ts[1]["args"]["b"]) == 12 and float(ts[1]["result"]) == 9600, "基础金额、次数或工具结果不符。")
        sources = result["evidence"]
        check(any(s["document_id"] == row["id"] and "800" in s["quote"] and "半年" in s["quote"] for s in sources), "引文没有同时保留金额、条件及实际文档身份。")
        payload = self.retrieval_meta(result)
        self.note("retrieval_actual", payload["retrieval"])
        check(payload["retrieval"]["rerank_status"] == self.config.get("EXPECT_RERANK", "applied"), "真实重排状态与本次验收配置不符；fallback不计作GPU重排通过。")
        unknown_sid = self.session("unknown", "agent")
        unknown, _ = self.turn("unknown", unknown_sid,
            "请调用search_handbook查询公司停车费每月报销上限。只依据本次原文；未说明时选择insufficient且不引用其他费用。")
        check(unknown["status"] == "done" and unknown["answer_status"] == "insufficient" and not unknown["evidence"], "停车费未知项没有正确处理。")
        check([t["name"] for t in unknown["tools"]] == ["search_handbook"], "未知题应实际查询。")
        self.data["sessions"]["rag"]["signature"] = signature(result)
        return "住房规则原文与9600工具结果通过；停车费明确未知；重排状态已记录。"

    def approvals(self):
        for approved in [True, False]:
            label = "approve" if approved else "reject"
            sid = self.session(label, "agent")
            recipient = "结课模拟用户甲" if approved else "结课模拟用户乙"
            content = "本条仅用于第十部分本地模拟验收。"
            waiting, _ = self.turn(label + "_pending", sid,
                f'请调用send_notice。完整收件人是“{recipient}”，正文是“{content}”，请原样保留。')
            check(waiting["status"] == "awaiting_approval" and waiting["send_count"] == 0 and len(waiting["pending"]) == 1, "没有在执行前暂停审批。")
            pending = waiting["pending"][0]
            check(pending["value"]["args"] == {"recipient": recipient, "content": content}, "待审批参数改写了原始需求。")
            body = {"request_id": str(uuid4()), "approved": approved}
            self.operation(sid, "resume", body, pending["interrupt_id"])
            path = f"/admin/sessions/{sid}/approvals/{pending['interrupt_id']}"
            final = self.note(label, self.client.call("POST", path, "admin", body))
            check(final["status"] == "done" and not final["pending"] and final["send_count"] == int(approved), "审批后流程或发送次数错误。")
            check(final["answer_status"] == "verified_tool_result", "审批结论没有来自程序确认的工具记录。")
            check(self.client.call("POST", path, "admin", body) == final, "同审批ID未返回原缓存。")
            check(signature(self.state(label + "_current", sid)) == signature(final), "重复审批改变了状态。")
            rows = self.client.call("GET", "/admin/approvals?limit=100", "admin")
            record = next(r for r in rows if r["session_id"] == sid and r["interrupt_id"] == pending["interrupt_id"])
            self.note(label + "_record", record)
            check(record["decision_status"] == ("approved" if approved else "rejected") and
                  record["execution_status"] == ("succeeded" if approved else "not_started") and
                  record["decided_by"] == self.config["ADMIN_USERNAME"], "审批业务记录与实际决定不符。")
            if not approved:
                check("审批人拒绝" in final["reply"] and f"{recipient}拒绝" not in final["reply"], "拒绝结论归错了角色。")
            self.data["sessions"][label]["signature"] = signature(final)
        return "两条本次模拟通知：批准发1次，拒绝发0次，同ID重放均不增加执行。"

    def documents(self):
        name = "core_" + uuid4().hex[:12] + ".txt"
        marker = "结课校验补贴" + uuid4().hex[:6]
        text = f"# 本机教学临时规则\n{marker}的月标准为137元，完成本次本地练习即可申请。\n"
        row = self.client.upload(name, text.encode("utf-8"))
        self.note("temporary_document", row)
        try:
            sid = self.session("document_before", "agent")
            before, _ = self.turn("document_before", sid,
                f"请先调用search_handbook查询{name}中{marker}的月标准和申请条件，再选择对应规则原句。")
            check(before["answer_status"] == "evidence_checked" and any(
                s["document_id"] == row["id"] and "137" in s["quote"] and marker in s["quote"]
                for s in before["evidence"]), "新文档没有进入真实检索并被正确引用。")
        finally:
            # 只删除本次POST实际返回的文档ID，不按文件名批量删除。
            self.client.call("DELETE", "/admin/documents/" + row["id"], "admin", expected=204)
            self.note("temporary_document_deleted", {"id": row["id"]})
        check(not any(d["id"] == row["id"] for d in self.client.call("GET", "/admin/documents", "admin")), "列表仍有本次临时文档。")
        sid = self.session("document_after", "agent")
        after, _ = self.turn("document_after", sid,
            f"请先调用search_handbook查询{name}中{marker}的月标准。仅依据当前工具原文，找不到该规则时选择insufficient。")
        check(after["answer_status"] == "insufficient", "删除后新会话仍宣称知道临时规则。")
        payload = self.retrieval_meta(after)
        check(not any(c["document_id"] == row["id"] for c in payload["candidates"]), "删除的文档仍在新检索候选中。")
        self.data["sessions"]["document_before"]["signature"] = signature(before)
        return "本次临时文档上传→可检索→删除→新会话未知；旧回答保留。"


def credentials():
    cfg = dict(dotenv_values(ROOT / ".env.acceptance"))
    required = ["EMPLOYEE_USERNAME", "EMPLOYEE_PASSWORD", "ADMIN_USERNAME", "ADMIN_PASSWORD"]
    check(all(cfg.get(k) for k in required), "请先复制.env.acceptance.example为.env.acceptance并填写本机账号。")
    return cfg


def readback(directory, config, base):
    data = json.loads((directory / "results.json").read_text(encoding="utf-8"))
    c = Client(base or data["base_url"])
    run = CoreRun(c, directory, config)
    run.data = data
    try:
        health = c.call("GET", "/health")
        old = data["evidence"]["health"]
        check(health.get("build") == "stage10-final" and health.get("instance_id")
              and health["instance_id"] != old["instance_id"], "没有观察到新的服务实例；请先正常停止并重启服务。")
        c.login("employee", config["EMPLOYEE_USERNAME"], config["EMPLOYEE_PASSWORD"])
        checked = 0
        for row in data["sessions"].values():
            if "signature" not in row:
                continue
            state = c.call("GET", "/sessions/" + row["sid"], "employee")
            check(signature(state) == row["signature"], "重启后会话、检查点或消息不一致：" + row["sid"])
            checked += 1
        check(checked > 0, "没有可核对的完整会话，不能登记重启通过。")
        for item in data["operations"]:
            op = c.call("GET", f"/sessions/{item['sid']}/operations/{item['body']['request_id']}", "employee")
            check(op["status"] == "completed", "原操作尚未确认完成。")
        data["readback"] = f"PASS：新实例只读核对{checked}个原会话和{len(data['operations'])}条操作；没有调用invoke。"
    except Exception as exc:
        data["readback"] = f"FAIL：{type(exc).__name__}: {exc}"
        run.persist()
        raise
    run.persist()
    print(data["readback"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    run = sub.add_parser("run")
    run.add_argument("--base-url", default="http://127.0.0.1:8000")
    run.add_argument("--only", nargs="+", choices=["G1", "G2", "G3", "G4", "G5"],
                     help="仅用于修复后的定向复测；不会登记其他组通过。")
    back = sub.add_parser("readback")
    back.add_argument("--run", required=True, type=Path)
    back.add_argument("--base-url")
    args = parser.parse_args()
    config = credentials()
    if args.action == "readback":
        readback(args.run.resolve(), config, args.base_url)
        return
    directory = ROOT / "artifacts" / (datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6])
    directory.mkdir(parents=True)
    core = CoreRun(Client(args.base_url), directory, config)
    requested = list(dict.fromkeys(args.only or ["G1", "G2", "G3", "G4", "G5"]))
    core.data["requested_groups"] = requested
    core.persist()
    print("本次报告目录：", directory)
    if "G1" in requested:
        core.group("G1 身份与头像", core.identity)
    else:
        try:
            health = core.client.call("GET", "/health")
            check(health.get("build") == "stage10-final", "当前端口不是正式版本。")
            core.note("health", health)
            for key, prefix in [("employee", "EMPLOYEE"), ("admin", "ADMIN")]:
                core.client.login(key, config[prefix + "_USERNAME"], config[prefix + "_PASSWORD"])
        except Exception as exc:
            core.group("前置登录", lambda: check(False, str(exc)))
    if {"employee", "admin"}.issubset(core.client.tokens):
        for code, name, action in [("G2", "G2 持久聊天与真实流式", core.chat), ("G3", "G3 制度依据与计算", core.handbook),
                                  ("G4", "G4 模拟通知审批", core.approvals), ("G5", "G5 文档生命周期", core.documents)]:
            if code in requested:
                core.group(name, action)
    core.persist()
    print("报告：", directory / "report.md")
    print("服务正常重启后，只读复核：uv run python -m acceptance.core readback --run", '"' + str(directory) + '"')
    passed = len(core.data["groups"]) == len(requested) and all(g["status"] == "PASS" for g in core.data["groups"])
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
