"""会话/操作缓存和消息业务副本；保留原事务、SQL表名与消息ID。"""
import json
from uuid import uuid4
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from langchain_core.messages import AIMessage, ToolMessage, messages_from_dict, messages_to_dict
from app.config import get_db_options
from app.db import open_db
from app.errors import AppError
from app.repositories.users import UserRepository


class SessionRepository:
    def setup(self):
        with open_db() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS study.api_sessions (
                id UUID PRIMARY KEY, owner_id BIGINT NOT NULL REFERENCES study.api_users(id),
                internal_thread TEXT NOT NULL UNIQUE,
                mode TEXT NOT NULL CHECK(mode IN ('chat','agent')),
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS study.api_operations (
                session_id UUID NOT NULL REFERENCES study.api_sessions(id) ON DELETE CASCADE,
                request_id UUID NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('turn','resume')),
                fingerprint TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('running','completed','uncertain')),
                result_json JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY(session_id,request_id),
                CHECK ((status='completed') = (result_json IS NOT NULL))
            )""")

    def actor(self, user, admin=False):
        return UserRepository().actor(user, admin=admin)

    def create(self, owner_id, mode):
        sid = uuid4()
        with open_db() as conn:
            row = conn.execute("""INSERT INTO study.api_sessions
                (id,owner_id,internal_thread,mode) VALUES (%s,%s,%s,%s)
                RETURNING id,mode,created_at""",
                (sid, owner_id, f"api8:{sid}", mode)).fetchone()
        return row

    def owned(self, owner_id, sid):
        with open_db() as conn:
            row = conn.execute("SELECT * FROM study.api_sessions WHERE id=%s AND owner_id=%s",
                               (sid, owner_id)).fetchone()
        if row is None:
            raise AppError(404, "session_not_found", "会话不存在。")
        return row

    def any_session(self, sid):
        with open_db() as conn:
            row = conn.execute("SELECT * FROM study.api_sessions WHERE id=%s", (sid,)).fetchone()
        if row is None:
            raise AppError(404, "session_not_found", "会话不存在。")
        return row

    def list_owned(self, owner_id):
        with open_db() as conn:
            return conn.execute("""SELECT id,mode,created_at FROM study.api_sessions
                WHERE owner_id=%s ORDER BY created_at,id LIMIT 100""", (owner_id,)).fetchall()

    def operation(self, sid, rid):
        with open_db() as conn:
            return conn.execute("""SELECT request_id,kind,fingerprint,status,result_json
                FROM study.api_operations WHERE session_id=%s AND request_id=%s""",
                (sid, rid)).fetchone()

    def unfinished(self, sid):
        with open_db() as conn:
            return conn.execute("""SELECT request_id FROM study.api_operations
                WHERE session_id=%s AND status IN ('running','uncertain') LIMIT 1""",
                (sid,)).fetchone() is not None

    def start(self, sid, rid, kind, fingerprint):
        with open_db() as conn:
            conn.execute("""INSERT INTO study.api_operations
                (session_id,request_id,kind,fingerprint,status) VALUES (%s,%s,%s,%s,'running')""",
                (sid, rid, kind, fingerprint))

    def finish(self, sid, rid, result):
        with open_db() as conn:
            row = conn.execute("""UPDATE study.api_operations SET status='completed',result_json=%s,
                updated_at=CURRENT_TIMESTAMP WHERE session_id=%s AND request_id=%s
                AND status='running' RETURNING request_id""", (Jsonb(result), sid, rid)).fetchone()
            if row is None:
                raise RuntimeError("操作完成结果未写入，不能返回已缓存成功。")

    def uncertain(self, sid, rid):
        with open_db() as conn:
            conn.execute("""UPDATE study.api_operations SET status='uncertain',
                updated_at=CURRENT_TIMESTAMP WHERE session_id=%s AND request_id=%s
                AND status='running'""", (sid, rid))


def json_messages(messages) -> list[dict]:
    return json.loads(
        json.dumps(messages_to_dict(messages), ensure_ascii=False, allow_nan=False)
    )


def snapshot_to_record(thread_id: str, snapshot) -> dict:
    history = snapshot.values.get("messages", [])
    if not history:
        raise ValueError("检查点没有消息，不创建空的业务副本。")

    checkpoint_id = snapshot.config["configurable"].get("checkpoint_id")
    if not checkpoint_id:
        raise ValueError("缺少来源检查点 ID。")
    payloads = json_messages(history)
    seen_ids = set()
    for message, payload in zip(history, payloads, strict=True):
        if message.type not in {"system", "human", "ai", "tool"}:
            raise ValueError(f"本课不支持的消息角色：{message.type}")
        if not isinstance(message.id, str) or not message.id.strip():
            raise ValueError("消息缺少稳定 ID，不能用序号假装消息身份。")
        if message.id in seen_ids:
            raise ValueError("同一会话存在重复消息 ID。")
        seen_ids.add(message.id)
        if payload["data"]["id"] != message.id:
            raise ValueError("消息序列化前后 ID 不一致。")

    record = {
        "thread_id": thread_id,
        "checkpoint_id": checkpoint_id,
        "message_count": len(payloads),
        "model_calls": snapshot.values.get("model_calls", 0),
        "send_count": snapshot.values.get("send_count", 0),
        "pending_count": len(snapshot.interrupts),
        "messages": payloads,
    }
    for field in ["model_calls", "send_count", "pending_count"]:
        if type(record[field]) is not int or record[field] < 0:
            raise ValueError(f"{field} 必须是非负整数。")
    return record


def setup_tables() -> None:
    with psycopg.connect(**get_db_options()) as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS study")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS study.agent_sessions (
                    thread_id TEXT PRIMARY KEY,
                    checkpoint_id TEXT NOT NULL,
                    message_count INTEGER NOT NULL CHECK (message_count >= 0),
                    model_calls INTEGER NOT NULL CHECK (model_calls >= 0),
                    send_count INTEGER NOT NULL CHECK (send_count >= 0),
                    pending_count INTEGER NOT NULL CHECK (pending_count >= 0),
                    synced_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS study.agent_messages (
                    thread_id TEXT NOT NULL
                        REFERENCES study.agent_sessions(thread_id),
                    position INTEGER NOT NULL CHECK (position >= 1),
                    message_id TEXT NOT NULL,
                    role TEXT NOT NULL
                        CHECK (role IN ('system', 'human', 'ai', 'tool')),
                    payload JSONB NOT NULL,
                    PRIMARY KEY (thread_id, message_id),
                    UNIQUE (thread_id, position)
                )
            """)


def save_record(record: dict) -> int:
    rows = [
        (
            record["thread_id"],
            position,
            payload["data"]["id"],
            payload["type"],
            Jsonb(payload),
        )
        for position, payload in enumerate(record["messages"], start=1)
    ]

    # 会话信息更新、删除旧消息、插入全部新消息使用同一事务。
    with psycopg.connect(**get_db_options()) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO study.agent_sessions
                    (thread_id, checkpoint_id, message_count,
                     model_calls, send_count, pending_count)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (thread_id) DO UPDATE SET
                    checkpoint_id = EXCLUDED.checkpoint_id,
                    message_count = EXCLUDED.message_count,
                    model_calls = EXCLUDED.model_calls,
                    send_count = EXCLUDED.send_count,
                    pending_count = EXCLUDED.pending_count,
                    synced_at = CURRENT_TIMESTAMP
            """, (
                record["thread_id"], record["checkpoint_id"],
                record["message_count"], record["model_calls"],
                record["send_count"], record["pending_count"],
            ))
            cur.execute(
                "DELETE FROM study.agent_messages WHERE thread_id = %s",
                (record["thread_id"],),
            )
            replaced_count = cur.rowcount
            cur.executemany("""
                INSERT INTO study.agent_messages
                    (thread_id, position, message_id, role, payload)
                VALUES (%s, %s, %s, %s, %s)
            """, rows)
    return replaced_count


def read_business_session(thread_id: str) -> dict | None:
    # 一条 JOIN 查询同时取得元信息和消息，不先同步，不读取检查点。
    with psycopg.connect(**get_db_options(), row_factory=dict_row) as conn:
        rows = conn.execute("""
            SELECT s.thread_id, s.checkpoint_id, s.message_count,
                   s.model_calls, s.send_count, s.pending_count, s.synced_at,
                   m.position, m.message_id, m.role, m.payload
            FROM study.agent_sessions AS s
            LEFT JOIN study.agent_messages AS m
                ON m.thread_id = s.thread_id
            WHERE s.thread_id = %s
            ORDER BY m.position
        """, (thread_id,)).fetchall()

    if not rows:
        return None
    first = rows[0]
    message_rows = [row for row in rows if row["position"] is not None]
    positions = [row["position"] for row in message_rows]
    if positions != list(range(1, len(message_rows) + 1)):
        raise ValueError("业务消息顺序不连续。")
    if len(message_rows) != first["message_count"]:
        raise ValueError("业务会话的消息数与实际消息行数不一致。")

    history = messages_from_dict([row["payload"] for row in message_rows])
    for row, message in zip(message_rows, history, strict=True):
        if row["message_id"] != message.id or row["role"] != message.type:
            raise ValueError("业务列与消息 payload 不一致。")
    return {
        key: first[key]
        for key in [
            "thread_id", "checkpoint_id", "message_count", "model_calls",
            "send_count", "pending_count", "synced_at",
        ]
    } | {"messages": history}
