"""连接、全局执行锁和只读结构检查；不包含业务表DDL。"""
from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row
from app.config import get_db_options
from app.errors import AppError


def open_db(*, autocommit=False, **kwargs):
    options = dict(get_db_options())
    options.update(connect_timeout=5, row_factory=dict_row, autocommit=autocommit)
    options.update(kwargs)
    try:
        return psycopg.connect(**options)
    except psycopg.OperationalError as exc:
        raise AppError(503, "database_unavailable", "数据库连接暂时不可用。") from exc


EXECUTION_LOCK = 524108


@contextmanager
def execution_guard():
    with open_db(autocommit=True) as conn:
        locked = conn.execute("SELECT pg_try_advisory_lock(%s) AS ok",
                              (EXECUTION_LOCK,)).fetchone()["ok"]
        if not locked:
            raise AppError(409, "busy", "另一项模型或知识库操作正在执行，请稍后提交。")
        try:
            yield
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (EXECUTION_LOCK,))


TABLES = ["study.api_users", "study.api_sessions", "study.api_operations",
          "study.api_documents", "study.api_document_chunks", "study.agent_sessions",
          "study.agent_messages", "study.agent_approval_records", "public.checkpoints",
          "public.checkpoint_blobs", "public.checkpoint_writes", "public.checkpoint_migrations"]


def check_schema():
    with open_db() as conn:
        missing = [row["name"] for row in conn.execute(
            "SELECT t AS name,to_regclass(t) IS NOT NULL AS ok FROM unnest(%s::text[]) AS names(t)",
            (TABLES,)).fetchall() if not row["ok"]]
        if missing:
            raise AppError(503, "schema_not_ready", "请先运行app.manage setup；缺表：" + ",".join(missing))
        avatar = conn.execute("""SELECT EXISTS(SELECT 1 FROM information_schema.columns
            WHERE table_schema='study' AND table_name='api_users' AND column_name='avatar_key')
            AND EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='study.api_users'::regclass
            AND conname='study_avatar_key_allowed' AND convalidated) AS ok""").fetchone()["ok"]
        if not avatar:
            raise AppError(503, "profile_migration_missing", "头像字段或约束缺失，请运行app.manage setup。")
    return {"status": "ready", "checked": "tables_and_avatar_schema"}
