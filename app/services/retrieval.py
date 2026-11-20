"""向量/BM25召回、RRF融合、本地重排及显式降级；不定义HTTP或工具入口。"""
import hashlib
import json
import math
from dataclasses import dataclass, replace
from langchain_core.documents import Document
from app.core.llm import get_embeddings, get_reranker
from app.repositories.documents import DocumentRepository, to_document
from app.services.documents import encode_vector
from app.config import setting


RECALL_K, TOP_K, RRF_K = 5, 3, 60


@dataclass
class RetrievalResult:
    hits: list[tuple[float, Document]]
    requested_mode: str
    actual_mode: str
    score_kind: str
    digest: str
    candidate_count: int
    notes: tuple[str, ...] = ()
    rerank_status: str = "disabled"


def doc_key(doc):
    source = doc.metadata.get("source")
    chunk_id = doc.metadata.get("chunk_id")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("缺少来源。")
    if type(chunk_id) is not int or chunk_id < 1:
        raise ValueError("块编号必须是正整数。")
    return source, chunk_id


def tokenize(text):
    import jieba
    return [
        token for token in jieba.lcut(text.lower(), cut_all=False)
        if any(ch.isalnum() for ch in token)
    ]


def build_bm25(chunks):
    from rank_bm25 import BM25Okapi
    corpus = [tokenize(doc.page_content) for doc in chunks]
    if not corpus or not any(corpus):
        raise ValueError("没有可以建立BM25索引的词。")
    return BM25Okapi(corpus)


def search_bm25(question, chunks, bm25, limit=RECALL_K):
    tokens = tokenize(question)
    if not tokens:
        return []
    scores = [float(s) for s in bm25.get_scores(tokens)]
    if len(scores) != len(chunks) or not all (math.isfinite(s) for s in scores):
        raise ValueError("BM25分数数量或者数值不合法。")
    ranked = sorted(
        zip(scores, chunks, strict=True),
        key=lambda pair: (-pair[0], doc_key(pair[1]))
    )
    return [(score, doc) for score,doc in ranked if score >0][:limit]


def fuse_rrf(rankings, top_k=TOP_K, rrf_k=RRF_K):
    if type(top_k) is not int or type(rrf_k) is not int or min(top_k, rrf_k)<1:
        raise ValueError("top_k 和 rrf_k必须是整数。")
    scores, docs = {}, {}
    for ranking in rankings:
        seen = set()
        rank = 0
        for _, doc in ranking:
            key = doc_key(doc)
            if key in docs and docs[key].page_content != doc.page_content:
                raise ValueError("同一块身份出现不同原文，不能融合。")
            docs[key] = doc
            if key in seen:
                continue
            seen.add(key)
            rank += 1
            scores[key] = scores.get(key, 0.0) + 1.0 / (rrf_k+rank)

    keys = sorted(scores, key=lambda key: (-scores[key], key))[:top_k]
    return [(scores[key], docs[key]) for key in keys]


class RankedRetriever:
    def __init__(self, base, client=None, enabled=False, max_candidates=10,
                 simulated=False, config_note=None):
        if type(max_candidates) is not int or max_candidates < 1:
            raise ValueError("max_candidates 必须为正整数。")
        self.base, self.client, self.enabled = base, client, enabled
        self.max_candidates, self.simulated = max_candidates, simulated
        self.config_note = config_note

    def search(self, question, top_k=3):
        if type(top_k) is not int or not 1 <= top_k <= self.max_candidates:
            raise ValueError("top_k 必须在候选上限内。")
        result = self.base.search(question, top_k=self.max_candidates)
        if not result.hits:
            raise ValueError("召回为空，不能重排。")
        baseline = replace(result, hits=list(result.hits[:top_k]))
        if not self.enabled:
            return replace(baseline, rerank_status="disabled")
        if self.client is None:
            note = self.config_note or "rerank_unconfigured"
            return replace(baseline, rerank_status="fallback",
                           notes=baseline.notes + (note,))
        try:
            items = self.client.rank(
                question, [doc.page_content for _, doc in result.hits], top_n=top_k,
            )
            hits = [(item.score, result.hits[item.index][1]) for item in items]
            if not hits:
                raise ValueError("重排没有可用结果。")
        except Exception as exc:
            return replace(
                baseline, rerank_status="fallback",
                notes=baseline.notes + (f"rerank_failed:{type(exc).__name__}",),
            )
        info = self.client.info
        print("重排后端/实际设备：", info.get("backend"), info.get("device"))
        return replace(
            result, hits=hits,
            score_kind="mock_score" if self.simulated else "relevance_score",
            rerank_status="mock" if self.simulated else "applied",
            notes=result.notes + (f"rerank_backend:{info.get('backend', 'unknown')}",),
        )


class ApiRetriever:
    def __init__(self, store, rows, embeddings, mode):
        if mode not in {"vector", "hybrid"}:
            raise ValueError("RETRIEVAL_MODE必须是vector或hybrid。")
        if any(row["embedding_model"] != embeddings.model for row in rows):
            raise ValueError("索引模型与当前问题嵌入模型不一致。")
        self.store, self.rows, self.embeddings, self.mode = store, rows, embeddings, mode

    def search(self, question, top_k=3):
        if not question.strip() or type(top_k) is not int or top_k < 1:
            raise ValueError("检索问题与候选上限不合法。")
        chunks = [to_document(row) for row in self.rows]
        if not chunks:
            raise ValueError("当前没有可检索文档。")
        by_key = {doc_key(doc): doc for doc in chunks}
        encoded = encode_vector(self.embeddings.embed_query(question))
        vector = self.store.vector_hits(encoded, self.embeddings.model, limit=5)
        if not vector:
            raise ValueError("已登记文档没有可用向量。")
        for score, doc in vector:
            key = doc_key(doc)
            if (not math.isfinite(score) or key not in by_key
                    or doc.page_content != by_key[key].page_content):
                raise ValueError("向量候选与本次正文快照不一致。")
        bm25, notes = [], []
        if self.mode == "hybrid":
            try:
                bm25 = search_bm25(question, chunks, build_bm25(chunks), limit=5)
                if not bm25:
                    notes.append("bm25_no_positive_hits")
            except Exception as exc:
                notes.append(f"bm25_failed:{type(exc).__name__}")
        actual = "hybrid" if bm25 else "vector"
        hits = fuse_rrf([vector, bm25], top_k=10) if bm25 else vector
        snapshot = [(str(row["document_id"]), row["chunk_id"], row["content"])
                    for row in self.rows]
        digest = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")).hexdigest()
        return RetrievalResult(hits=list(hits[:top_k]), requested_mode=self.mode,
            actual_mode=actual, score_kind="rrf" if bm25 else "cosine_similarity",
            digest=digest, candidate_count=len(hits), notes=tuple(notes))


def ranked(base, rerank_mode):
    if rerank_mode == "off":
        return RankedRetriever(base)
    if rerank_mode != "local":
        raise ValueError("API真实检索只接受RERANK_MODE=off或local，不使用mock。")
    try:
        client = get_reranker()
    except Exception as exc:
        return RankedRetriever(base, enabled=True,
            config_note=f"rerank_init_failed:{type(exc).__name__}")
    return RankedRetriever(base, client, enabled=True)


def retrieve_text(question: str) -> str:
    """检索公司当前已入库的手册和制度文本，输入完整独立的问题。
    返回候选原文、文件名、文档ID和块编号；候选可能没有答案，资料不足时说明未知。
    """
    if not question.strip():
        raise ValueError("检索问题不能为空。")
    mode = setting("RETRIEVAL_MODE", "vector").strip().lower()
    rerank_mode = setting("RERANK_MODE", "off").strip().lower()
    store = DocumentRepository()
    rows = store.chunks()
    if not rows:
        print("API检索：actual=empty，候选=0，未生成问题向量，未重排。")
        return json.dumps({"candidates": [], "retrieval": {"actual_mode": "empty",
            "rerank_status": "not_run", "notes": ["empty_knowledge_base"]}}, ensure_ascii=False)
    base = ApiRetriever(store, rows, get_embeddings(), mode)
    result = ranked(base, rerank_mode).search(question, top_k=3)
    print("API检索请求/实际：", result.requested_mode, result.actual_mode)
    print("重排/分数/说明：", result.rerank_status, result.score_kind, result.notes)
    print("候选总数/快照指纹：", result.candidate_count, result.digest)
    candidates = []
    for rank, (score, doc) in enumerate(result.hits, 1):
        m = doc.metadata
        print(f"[E{rank}] 分数={score:.6f}，文件={m['filename']}，文档={m['document_id']}，块={m['chunk_id']}")
        candidates.append({"source_id": f"{m['document_id']}:{m['chunk_id']}",
            "filename": m["filename"], "document_id": m["document_id"],
            "chunk_id": m["chunk_id"], "content": doc.page_content, "score": score})
    return json.dumps({"candidates": candidates, "retrieval": {
        "requested_mode": result.requested_mode, "actual_mode": result.actual_mode,
        "rerank_status": result.rerank_status, "score_kind": result.score_kind,
        "notes": result.notes, "digest": result.digest}}, ensure_ascii=False, allow_nan=False)
