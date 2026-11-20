"""审批请求、决定和执行结果的持久化；管理列表也从这里查询。"""
import json
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from app.config import get_db_options
from app.db import open_db


def required_text(value, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串。")
    return value.strip()


def setup_table() -> None:
    with psycopg.connect(**get_db_options()) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS study")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS study.agent_approval_records (
                thread_id TEXT NOT NULL,
                interrupt_id TEXT NOT NULL,
                tool_call_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                args_json JSONB NOT NULL
                    CHECK (jsonb_typeof(args_json) = 'object'),
                origin TEXT NOT NULL CHECK (origin IN ('demo', 'agent')),
                decision_status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (decision_status IN ('pending', 'approved', 'rejected')),
                execution_status TEXT NOT NULL DEFAULT 'not_started'
                    CHECK (execution_status IN
                           ('not_started', 'succeeded', 'failed', 'unknown')),
                decided_by TEXT,
                decided_at TIMESTAMPTZ,
                result_text TEXT,
                result_recorded_at TIMESTAMPTZ,
                registered_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (thread_id, interrupt_id),
                CHECK (
                    (decision_status = 'pending'
                     AND decided_by IS NULL AND decided_at IS NULL)
                    OR
                    (decision_status IN ('approved', 'rejected')
                     AND decided_by IS NOT NULL AND btrim(decided_by) <> ''
                     AND decided_at IS NOT NULL)
                ),
                CHECK (
                    (execution_status = 'not_started'
                     AND result_text IS NULL AND result_recorded_at IS NULL)
                    OR
                    (execution_status IN ('succeeded', 'failed', 'unknown')
                     AND decision_status = 'approved'
                     AND result_text IS NOT NULL AND btrim(result_text) <> ''
                     AND result_recorded_at IS NOT NULL)
                )
            )
        """)


def register_request(
    thread_id: str, interrupt_id: str, tool_call_id: str,
    tool_name: str, args: dict, *, origin: str,
) -> bool:
    thread_id = required_text(thread_id, "thread_id")
    interrupt_id = required_text(interrupt_id, "interrupt_id")
    tool_call_id = required_text(tool_call_id, "tool_call_id")
    tool_name = required_text(tool_name, "tool_name")
    if origin not in {"demo", "agent"}:
        raise ValueError("origin 只允许 demo 或 agent。")
    if not isinstance(args, dict):
        raise ValueError("args 必须是字典。")
    args = json.loads(json.dumps(args, ensure_ascii=False, allow_nan=False))

    with psycopg.connect(**get_db_options(), row_factory=dict_row) as conn:
        inserted = conn.execute("""
            INSERT INTO study.agent_approval_records
                (thread_id, interrupt_id, tool_call_id, tool_name, args_json, origin)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (thread_id, interrupt_id) DO NOTHING
            RETURNING interrupt_id
        """, (thread_id, interrupt_id, tool_call_id, tool_name, Jsonb(args), origin))
        if inserted.fetchone() is not None:
            return True

        old = conn.execute("""
            SELECT tool_call_id, tool_name, args_json, origin
            FROM study.agent_approval_records
            WHERE thread_id = %s AND interrupt_id = %s
        """, (thread_id, interrupt_id)).fetchone()
        if old is None:
            raise ValueError("记录冲突后未读到原请求。")
        expected = {
            "tool_call_id": tool_call_id, "tool_name": tool_name,
            "args_json": args, "origin": origin,
        }
        if any(old[key] != value for key, value in expected.items()):
            raise ValueError("相同中断的工具、参数或来源冲突，不允许改写。")
        return False


def decide_request(
    thread_id: str, interrupt_id: str, approved: bool, decided_by: str,
) -> bool:
    thread_id = required_text(thread_id, "thread_id")
    interrupt_id = required_text(interrupt_id, "interrupt_id")
    decided_by = required_text(decided_by, "decided_by")
    if type(approved) is not bool:
        raise ValueError("approved 必须是 bool，不能用字符串代替。")
    status = "approved" if approved else "rejected"

    with psycopg.connect(**get_db_options()) as conn:
        changed = conn.execute("""
            UPDATE study.agent_approval_records
            SET decision_status = %s, decided_by = %s,
                decided_at = CURRENT_TIMESTAMP
            WHERE thread_id = %s AND interrupt_id = %s
              AND decision_status = 'pending'
            RETURNING interrupt_id
        """, (status, decided_by, thread_id, interrupt_id)).fetchone()
        if changed is not None:
            return True
        exists = conn.execute("""
            SELECT decision_status FROM study.agent_approval_records
            WHERE thread_id = %s AND interrupt_id = %s
        """, (thread_id, interrupt_id)).fetchone()
        if exists is None:
            raise ValueError("审批请求不存在。")
        return False


def record_result(
    thread_id: str, interrupt_id: str, execution_status: str, result_text: str,
) -> bool:
    thread_id = required_text(thread_id, "thread_id")
    interrupt_id = required_text(interrupt_id, "interrupt_id")
    result_text = required_text(result_text, "result_text")
    if execution_status not in {"succeeded", "failed", "unknown"}:
        raise ValueError("结果状态只允许 succeeded、failed 或 unknown。")

    with psycopg.connect(**get_db_options(), row_factory=dict_row) as conn:
        changed = conn.execute("""
            UPDATE study.agent_approval_records
            SET execution_status = %s, result_text = %s,
                result_recorded_at = CURRENT_TIMESTAMP
            WHERE thread_id = %s AND interrupt_id = %s
              AND decision_status = 'approved'
              AND execution_status = 'not_started'
            RETURNING interrupt_id
        """, (execution_status, result_text, thread_id, interrupt_id)).fetchone()
        if changed is not None:
            return True
        old = conn.execute("""
            SELECT decision_status, execution_status, result_text
            FROM study.agent_approval_records
            WHERE thread_id = %s AND interrupt_id = %s
        """, (thread_id, interrupt_id)).fetchone()
        if old is None:
            raise ValueError("审批请求不存在。")
        if old["decision_status"] != "approved":
            raise ValueError("尚未批准，不能登记执行结果。")
        if (old["execution_status"], old["result_text"]) != (
            execution_status, result_text
        ):
            raise ValueError("已有结果与本次冲突，不允许覆盖。")
        return False


def read_records(thread_id: str) -> list[dict]:
    with psycopg.connect(**get_db_options(), row_factory=dict_row) as conn:
        return conn.execute("""
            SELECT thread_id, interrupt_id, tool_call_id, tool_name, args_json,
                   origin, decision_status, decided_by, decided_at,
                   execution_status, result_text, result_recorded_at, registered_at
            FROM study.agent_approval_records
            WHERE thread_id = %s
            ORDER BY interrupt_id
        """, (thread_id,)).fetchall()


def list_approvals(decision, limit):
    with open_db() as conn:
        return conn.execute("""SELECT s.id AS session_id,u.username AS owner,
            r.interrupt_id,r.tool_call_id,r.tool_name,r.args_json,r.origin,
            r.decision_status,r.execution_status,r.decided_by,r.decided_at,
            r.result_text,r.result_recorded_at,r.registered_at
            FROM study.agent_approval_records r
            JOIN study.api_sessions s ON s.internal_thread=r.thread_id
            JOIN study.api_users u ON u.id=s.owner_id
            WHERE (CAST(%s AS TEXT) IS NULL OR r.decision_status=%s)
            ORDER BY r.registered_at DESC,r.interrupt_id LIMIT %s""",
            (decision, decision, limit)).fetchall()
