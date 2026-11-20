"""统一创建聊天、嵌入与重排客户端；模型只在实际使用时加载。"""
from functools import lru_cache
from langchain_ollama import ChatOllama, OllamaEmbeddings
from app.config import setting, EMBEDDING_MODEL
from app.errors import ChatServiceError

@lru_cache(maxsize=2)
def get_llm(num_predict=128):
    model_id, base_url = setting("OLLAMA_MODEL_ID"), setting("OLLAMA_BASE_URL")
    if not model_id or not base_url:
        raise ChatServiceError("缺少OLLAMA_MODEL_ID或OLLAMA_BASE_URL。")
    try:
        return ChatOllama(model=model_id, base_url=base_url, temperature=0.2,
            num_predict=num_predict, validate_model_on_init=False, client_kwargs={"timeout": 60.0})
    except Exception as exc:
        raise ChatServiceError("模型客户端初始化失败。") from exc

@lru_cache(maxsize=1)
def get_embeddings():
    model_id, base_url = setting("OLLAMA_EMBEDDING_MODEL_ID"), setting("OLLAMA_BASE_URL")
    if model_id != EMBEDDING_MODEL or not base_url:
        raise ValueError("要求OLLAMA_EMBEDDING_MODEL_ID=bge-m3，并配置OLLAMA_BASE_URL。")
    return OllamaEmbeddings(model=model_id, base_url=base_url)

@lru_cache(maxsize=1)
def get_reranker():
    from app.core.rerank import local_client_from_env
    return local_client_from_env()
