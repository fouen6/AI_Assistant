"""PostgresSaver连接和序列化白名单；不编排业务节点。"""
from contextlib import contextmanager
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from app.db import open_db


def make_serializer() -> JsonPlusSerializer:
    return JsonPlusSerializer(
        allowed_msgpack_modules=[
            ("langchain_core.messages.system", "SystemMessage"),
            ("langchain_core.messages.human", "HumanMessage"),
            ("langchain_core.messages.ai", "AIMessage"),
            ("langchain_core.messages.tool", "ToolMessage"),
            ("langgraph.types", "Interrupt"),
        ]
    )


@contextmanager
def open_checkpointer():
    with open_db(autocommit=True, prepare_threshold=0) as conn:
        yield PostgresSaver(conn, serde=make_serializer())


def setup_checkpoints():
    with open_checkpointer() as saver:
        saver.setup()
