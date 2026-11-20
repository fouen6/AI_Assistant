"""所有环境配置在这里读取；导入模块不会连接数据库或加载模型。"""
import os
from functools import lru_cache
from pathlib import Path
from dotenv import load_dotenv, dotenv_values

ROOT = Path(__file__).resolve().parents[1]
MAX_MODEL_CALLS = 4
EMBEDDING_MODEL = "bge-m3"

@lru_cache(maxsize=1)
def load_configuration():
    load_dotenv(ROOT / ".env")

def setting(name, default=""):
    load_configuration()
    return os.getenv(name, default).strip()


def get_db_options()-> dict :
    project_dir = ROOT
    config_path = project_dir / "infra" / "postgres" / ".env.db"
    settings = dotenv_values(config_path)
    password = settings.get("POSTGRES_PASSWORD")
    if not password or password == "replace_with_your_local_password":
        raise ValueError("请先在 infra/postgres/.env.db 设置自己的数据库密码。")

    return {
        "host": "127.0.0.1",
        "port": 5433,
        "dbname": "ai_assistant",
        "user": "ai_study",
        "password": password,
        "connect_timeout": 5,
    }
