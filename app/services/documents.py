"""文档校验、切分、向量生成与入库编排；SQL位于repositories/documents.py。"""
import hashlib
import json
import math
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from app.core.llm import get_embeddings
from app.errors import AppError


def split_handbook(
        document:Document,
        chunk_size: int = 200,
        chunk_overlap: int = 50
)-> list[Document]:
    if not 0 <= chunk_overlap < chunk_size:
        raise ValueError("要求 chunk_size > 0, 且0 <= chunk_overlap < chunk_size.")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size = chunk_size,
        chunk_overlap = chunk_overlap,
        length_function = len,
        separators=["\n\n", "\n", "。", "；", "，", " ", ""]
    )

    chunks = splitter.split_documents([document])

    for index, chunk in enumerate(chunks,start=1):
        chunk.metadata["chunk_id"] = index
    return chunks


MAX_BYTES = 5 * 1024 * 1024


def validate_text(name, raw):
    if (not isinstance(name, str) or not name.strip() or len(name) > 120
            or name != name.strip() or any(c in name for c in ("/", "\\", "\x00"))
            or name in {".", ".."}):
        raise AppError(422, "invalid_filename", "请使用不含路径的短文件名。")
    if not name.lower().endswith((".txt", ".md")):
        raise AppError(422, "unsupported_document", "本课只接受UTF-8的txt/md文本。")
    if len(raw) > MAX_BYTES:
        raise AppError(413, "document_too_large", "单文件不能超过5MiB。")
    try:
        text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeDecodeError as exc:
        raise AppError(422, "invalid_encoding", "文件必须使用UTF-8编码。") from exc
    if not text.strip() or "\x00" in text:
        raise AppError(422, "empty_document", "文本不能空白或含NUL字符。")
    return text


def encode_vector(values):
    if (not isinstance(values, (list, tuple)) or len(values) != 1024
            or any(type(v) not in {int, float} or not math.isfinite(v) for v in values)
            or not any(values)):
        raise ValueError("必须是1024维有限非零数值向量，布尔值不是数值。")
    return json.dumps(values, allow_nan=False, separators=(",", ":"))


class DocumentService:
    def __init__(self, store, embeddings_factory=get_embeddings):
        self.store, self.embeddings_factory = store, embeddings_factory

    def upload(self, name, raw):
        text = validate_text(name, raw)
        if self.store.exists(name):
            raise AppError(409, "document_exists", "同名文档已存在，本课不自动覆盖。")
        chunks = split_handbook(Document(page_content=text, metadata={"source": name}), 200, 50)
        if not chunks or len(chunks) > 1000:
            raise AppError(413, "chunk_limit", "文本块数量应在1到1000之间。")
        try:
            embeddings = self.embeddings_factory()
            if embeddings.model != "bge-m3":
                raise ValueError("本课知识表固定bge-m3。")
            vectors = embeddings.embed_documents([c.page_content for c in chunks])
            if len(vectors) != len(chunks):
                raise ValueError("向量数量与文本块不一致。")
            encoded = [encode_vector(v) for v in vectors]
        except Exception as exc:
            raise AppError(503, "embedding_unavailable", "文档向量生成或校验失败，未写入文档。") from exc
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return self.store.insert(name, digest, embeddings.model, chunks, encoded)
