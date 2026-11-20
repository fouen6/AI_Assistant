"""用户表、角色事务、个人头像；所有用户SQL集中于此。"""
from psycopg.errors import UniqueViolation
from app.auth import hasher
from app.db import open_db, execution_guard
from app.errors import AppError


def public_user(row):
    return {k: row[k] for k in ("id", "username", "role", "is_active", "auth_version")}


class UserRepository:
    def setup(self):
        with open_db() as conn:
            conn.execute("CREATE SCHEMA IF NOT EXISTS study")
            conn.execute("""CREATE TABLE IF NOT EXISTS study.api_users (
                id BIGSERIAL PRIMARY KEY,
                username TEXT NOT NULL UNIQUE CHECK (username ~ '^[a-z][a-z0-9_]{2,31}$'),
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('employee','admin')),
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                auth_version INTEGER NOT NULL DEFAULT 0 CHECK (auth_version >= 0),
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")

    def by_name(self, username):
        with open_db() as conn:
            return conn.execute("SELECT * FROM study.api_users WHERE username=%s",
                                (username,)).fetchone()

    def by_id(self, user_id):
        with open_db() as conn:
            return conn.execute("SELECT * FROM study.api_users WHERE id=%s",
                                (user_id,)).fetchone()

    def create(self, username, password, role="employee"):
        if role not in {"employee", "admin"}:
            raise ValueError("无效角色")
        hashed = hasher().hash(password)
        try:
            with open_db() as conn:
                row = conn.execute("""INSERT INTO study.api_users
                    (username,password_hash,role) VALUES (%s,%s,%s) RETURNING *""",
                    (username, hashed, role)).fetchone()
            return public_user(row)
        except UniqueViolation as exc:
            raise AppError(409, "username_exists", "用户名已存在。") from exc

    def list_users(self):
        with open_db() as conn:
            rows = conn.execute("SELECT * FROM study.api_users ORDER BY id LIMIT 100").fetchall()
            return [public_user(r) for r in rows]

    def actor(self, user, admin=False):
        current = self.by_id(user["id"])
        if (current is None or not current["is_active"]
                or current["auth_version"] != user["auth_version"]):
            raise AppError(401, "identity_changed", "账号状态已变化，请重新登录。")
        if admin and current["role"] != "admin":
            raise AppError(403, "admin_required", "需要管理员权限。")
        return current

    def change_user(self, actor, user_id, role, active):
        with execution_guard():
            self.actor(actor, admin=True)
            with open_db() as conn:
                target = conn.execute("""SELECT id,username,role,is_active,auth_version
                    FROM study.api_users WHERE id=%s FOR UPDATE""", (user_id,)).fetchone()
                if target is None:
                    raise AppError(404, "user_not_found", "用户不存在。")
                old_admin = target["role"] == "admin" and target["is_active"]
                new_admin = role == "admin" and active
                if old_admin and not new_admin:
                    count = conn.execute("""SELECT count(*) AS n FROM study.api_users
                        WHERE role='admin' AND is_active""").fetchone()["n"]
                    if count <= 1:
                        raise AppError(409, "last_admin", "不能停用或降级最后一个有效管理员。")
                if target["role"] == role and target["is_active"] == active:
                    return public_user(target)
                row = conn.execute("""UPDATE study.api_users SET role=%s,is_active=%s,
                    auth_version=auth_version+1 WHERE id=%s
                    RETURNING id,username,role,is_active,auth_version""",
                    (role, active, user_id)).fetchone()
                return public_user(row)

    def setup_profile(self):
        with open_db() as conn:
            conn.execute("""ALTER TABLE study.api_users ADD COLUMN IF NOT EXISTS
                avatar_key TEXT NOT NULL DEFAULT 'person'""")
            # 不对旧数据做隐式重写；课程只允许下列键，发现旧库冲突先人工检查。
            bad = conn.execute("""SELECT count(*) AS n FROM study.api_users
                WHERE avatar_key IS NULL OR avatar_key NOT IN ('person','robot','cat','plant')""").fetchone()["n"]
            if bad:
                raise ValueError("已有头像键不符合本课约定，未重写旧资料。")
            exists = conn.execute("""SELECT 1 FROM pg_constraint WHERE
                conrelid='study.api_users'::regclass AND conname='study_avatar_key_allowed'""").fetchone()
            if exists is None:
                conn.execute("""ALTER TABLE study.api_users ADD CONSTRAINT study_avatar_key_allowed
                    CHECK (avatar_key IN ('person','robot','cat','plant'))""")

    def read_profile(self, user_id):
        with open_db() as conn:
            row = conn.execute("SELECT id,avatar_key FROM study.api_users WHERE id=%s",
                (user_id,)).fetchone()
        if row is None:
            raise AppError(404, "user_not_found", "用户不存在。")
        return row

    def change_avatar(self, user, avatar_key):
        if avatar_key not in {"person", "robot", "cat", "plant"}:
            raise AppError(422, "invalid_avatar", "不支持这个头像键。")
        with execution_guard():
            self.actor(user)
            with open_db() as conn:
                return conn.execute("""UPDATE study.api_users SET avatar_key=%s
                    WHERE id=%s RETURNING id,avatar_key""",
                    (avatar_key, user["id"])).fetchone()
