"""唯一维护入口：setup、doctor、serve和首次管理员创建。"""
import argparse
import getpass
import json
import logging
import re
from pathlib import Path
from urllib.request import urlopen
from app.config import ROOT, setting, load_configuration



def setup():
    from app.repositories.users import UserRepository
    from app.repositories.sessions import SessionRepository
    from app.repositories.documents import DocumentRepository
    from app.repositories.sessions import setup_tables
    from app.repositories.approvals import setup_table
    from app.core.checkpoint import setup_checkpoints
    for label, action in [
        ("用户", UserRepository().setup), ("会话与操作", SessionRepository().setup),
        ("知识库", DocumentRepository().setup), ("头像迁移", UserRepository().setup_profile),
        ("消息业务副本", setup_tables), ("审批业务记录", setup_table),
    ]:
        action()
        print(f"已就绪：{label}")
    setup_checkpoints()
    from app.db import check_schema
    check_schema()
    print("初始化完成。没有创建账号、上传文档或发送消息。")


def doctor():
    from app.db import check_schema
    from app.auth import get_tokens
    get_tokens()  # 仅校验签名密钥长度，不签发或打印令牌。
    print("数据库：", check_schema()["checked"])
    base = setting("OLLAMA_BASE_URL", "").rstrip("/")
    if not base:
        raise ValueError("缺少OLLAMA_BASE_URL。")
    with urlopen(base + "/api/tags", timeout=5) as response:
        tags = {item["name"] for item in json.load(response)["models"]}
    for key in ["OLLAMA_MODEL_ID", "OLLAMA_EMBEDDING_MODEL_ID"]:
        name = setting(key, "").strip()
        if name not in tags and name + ":latest" not in tags:
            raise ValueError(f"Ollama没有配置的模型：{key}={name}")
        print(key + "：", name)
    mode = setting("RERANK_MODE", "off")
    if mode not in {"off", "local"}:
        raise ValueError("正式版RERANK_MODE只接受off或local。")
    if mode == "local":
        import torch
        device = setting("RERANK_DEVICE", "cuda:0")
        if not device.startswith("cuda:") or not torch.cuda.is_available():
            raise ValueError("本课本地重排要求可用的CUDA设备。")
        cache = Path(setting("RERANK_CACHE_DIR", "models/rerank_cache"))
        cache = cache if cache.is_absolute() else ROOT / cache
        if not cache.is_dir():
            raise ValueError("重排缓存目录不存在，请填写原项目缓存的绝对路径。")
        print("重排配置：local /", device, "/", torch.cuda.get_device_name(int(device.split(":")[1])))
    print("诊断通过；尚未推理。真实检索与GPU重排结果以核心验收报告为准。")


def bootstrap(username):
    from app.repositories.users import UserRepository
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,31}", username):
        raise ValueError("用户名格式不合法。")
    store = UserRepository()
    if store.by_name(username) is not None:
        print("该用户名已存在，保留其角色和密码；未创建新管理员。")
        return
    password = getpass.getpass("设置管理员密码（12–128字符，不显示）：")
    if not 12 <= len(password) <= 128 or password != getpass.getpass("再次输入："):
        raise ValueError("长度不合法或两次输入不一致。")
    print("管理员已创建：", store.create(username, password, role="admin"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ["setup", "doctor"]:
        sub.add_parser(name)
    admin = sub.add_parser("bootstrap-admin")
    admin.add_argument("--username", default="study_admin")
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    load_configuration()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if args.action == "setup":
        setup()
    elif args.action == "doctor":
        doctor()
    elif args.action == "bootstrap-admin":
        bootstrap(args.username)
    else:
        import uvicorn
        uvicorn.run("app.main:app", host="127.0.0.1", port=args.port, reload=False)


if __name__ == "__main__":
    main()
