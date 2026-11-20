"""本地CrossEncoder CUDA推理与重排响应校验，不使用远程推理密钥。"""
from dataclasses import dataclass
import math
from pathlib import Path
from app.config import ROOT, setting


@dataclass(frozen=True)
class RerankItem:
    index: int
    score: float


class RerankClient:
    def __init__(self, transport):
        if not callable(transport):
            raise ValueError("transport 必须可调用。")
        self.transport = transport

    @property
    def info(self):
        return dict(getattr(self.transport, "info", {}))

    def rank(self, query, documents, top_n=3):
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query 必须是非空字符串。")
        if type(top_n) is not int or top_n < 1:
            raise ValueError("top_n 必须是正整数。")
        if not isinstance(documents, list) or any(
            not isinstance(text, str) or not text.strip() for text in documents
        ):
            raise ValueError("documents 必须是非空正文组成的列表。")
        if not documents:
            return []
        limit = min(top_n, len(documents))
        data = self.transport(query.strip(), list(documents), limit)
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise ValueError("缺少 results 数组。")
        rows = data["results"]
        if not 1 <= len(rows) <= limit:
            raise ValueError("重排结果数量不合法。")
        seen, result = set(), []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("重排项必须是对象。")
            index, score = row.get("index"), row.get("relevance_score")
            if type(index) is not int or not 0 <= index < len(documents):
                raise ValueError("重排下标越界或类型错误。")
            if index in seen:
                raise ValueError("重排下标重复。")
            if type(score) not in {int, float} or not math.isfinite(score):
                raise ValueError("重排分数必须是有限数值。")
            seen.add(index)
            result.append(RerankItem(index, float(score)))
        return sorted(result, key=lambda item: (-item.score, item.index))


class LocalGpuRerankTransport:
    def __init__(self, model_id, device="cuda:0", batch_size=4, max_length=512,
                 cache_dir=None, local_files_only=False, revision=None):
        if not isinstance(model_id, str) or not model_id.strip():
            raise ValueError("重排模型不能为空。")
        if not isinstance(device, str) or device not in {"cuda", "cuda:0"}:
            raise ValueError("本课固定使用第一张可见 GPU：cuda:0。")
        if type(batch_size) is not int or batch_size < 1:
            raise ValueError("batch_size 必须是正整数。")
        if type(max_length) is not int or max_length < 1:
            raise ValueError("max_length 必须是正整数。")

        # 放在此处导入：demo/off/mock 不需要安装或加载 GPU 模型。
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError("PyTorch CUDA 不可用；停止本地重排，不回退到 CPU。")
        from sentence_transformers import CrossEncoder

        self.torch = torch
        self.batch_size = batch_size
        self.encoder = CrossEncoder(
            model_id.strip(), device="cuda:0", backend="torch",
            max_length=max_length, cache_folder=cache_dir,
            local_files_only=local_files_only, revision=revision,
            model_kwargs={"torch_dtype": "float16"},
            activation_fn=torch.nn.Identity(),
            trust_remote_code=False,
        )
        underlying = getattr(self.encoder, "model", self.encoder)
        parameter = next(underlying.parameters())
        if parameter.device.type != "cuda" or parameter.device.index != 0:
            raise RuntimeError("实际模型参数不在 cuda:0，不能宣称 GPU 重排。")
        self.info = {
            "backend": "local_gpu", "model": model_id.strip(),
            "device": str(parameter.device), "dtype": str(parameter.dtype),
            "gpu_name": torch.cuda.get_device_name(0),
            "torch_version": str(torch.__version__),
            "cuda_runtime": torch.version.cuda,
            "model_revision": getattr(getattr(underlying, "config", None), "_commit_hash", None),
            "requested_revision": revision,
            "cache_dir": cache_dir,
            "batch_size": batch_size, "max_length": max_length,
            "local_files_only": local_files_only,
        }
        print("本地重排模型已加载：", self.info)

    def __call__(self, query, documents, top_n):
        pairs = [(query, text) for text in documents]
        with self.torch.inference_mode():
            scores = self.encoder.predict(
                pairs, batch_size=self.batch_size,
                show_progress_bar=False, convert_to_numpy=True,
            )
        values = [float(value) for value in scores]
        if len(values) != len(documents) or not all(math.isfinite(v) for v in values):
            raise ValueError("本地重排分数数量或数值不合法。")
        indexes = sorted(range(len(values)), key=lambda i: (-values[i], i))[:top_n]
        return {"results": [
            {"index": i, "relevance_score": values[i]} for i in indexes
        ]}


def local_client_from_env():
    project_dir = ROOT
    model_id = setting("RERANK_MODEL", "BAAI/bge-reranker-v2-m3").strip()
    device = setting("RERANK_DEVICE", "cuda:0").strip()
    cache_value = setting("RERANK_CACHE_DIR", "models/rerank_cache").strip()
    if not cache_value:
        raise ValueError("RERANK_CACHE_DIR 不能为空。")
    cache = Path(cache_value)
    if not cache.is_absolute():
        cache = project_dir / cache
    offline = setting("RERANK_LOCAL_FILES_ONLY", "0").strip()
    if offline not in {"0", "1"}:
        raise ValueError("RERANK_LOCAL_FILES_ONLY 必须是 0 或 1。")
    transport = LocalGpuRerankTransport(
        model_id=model_id, device=device,
        batch_size=int(setting("RERANK_BATCH_SIZE", "4")),
        max_length=int(setting("RERANK_MAX_LENGTH", "512")),
        cache_dir=str(cache.resolve()), local_files_only=offline == "1",
        revision=setting("RERANK_REVISION", "").strip() or None,
    )
    return RerankClient(transport)
