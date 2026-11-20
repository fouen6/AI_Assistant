"""知识库文档、分块、向量SQL；文档与块在同一事务中写入。"""
from uuid import uuid4
from psycopg.errors import UniqueViolation
from langchain_core.documents import Document
from app.db import open_db
from app.errors import AppError


def to_document(row):
    did = str(row["document_id"])
    return Document(page_content=row["content"], metadata={
        "source": f"{row['filename']} [document:{did}]",
        "filename": row["filename"], "document_id": did, "chunk_id": row["chunk_id"],
    })


class DocumentRepository:
    def setup(self):
        with open_db() as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute("""CREATE TABLE IF NOT EXISTS study.api_documents (
                id UUID PRIMARY KEY, filename TEXT UNIQUE NOT NULL,
                content_fingerprint TEXT NOT NULL, embedding_model TEXT NOT NULL,
                chunk_count INTEGER NOT NULL CHECK(chunk_count>0),
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS study.api_document_chunks (
                document_id UUID NOT NULL REFERENCES study.api_documents(id) ON DELETE CASCADE,
                chunk_id INTEGER NOT NULL CHECK(chunk_id>0), content TEXT NOT NULL,
                embedding vector(1024) NOT NULL,
                PRIMARY KEY(document_id,chunk_id)
            )""")

    def exists(self, name):
        with open_db() as conn:
            return conn.execute("SELECT 1 FROM study.api_documents WHERE filename=%s",
                                (name,)).fetchone() is not None

    def list(self):
        with open_db() as conn:
            return conn.execute("""SELECT id,filename,content_fingerprint,embedding_model,
                chunk_count,created_at FROM study.api_documents ORDER BY created_at,id
                LIMIT 100""").fetchall()

    def insert(self, name, digest, model, chunks, encoded):
        did = uuid4()
        try:
            with open_db() as conn:
                row = conn.execute("""INSERT INTO study.api_documents
                    (id,filename,content_fingerprint,embedding_model,chunk_count)
                    VALUES (%s,%s,%s,%s,%s) RETURNING *""",
                    (did, name, digest, model, len(chunks))).fetchone()
                for index, (chunk, vector) in enumerate(zip(chunks, encoded, strict=True), 1):
                    conn.execute("""INSERT INTO study.api_document_chunks
                        (document_id,chunk_id,content,embedding)
                        VALUES (%s,%s,%s,CAST(%s AS vector))""",
                        (did, index, chunk.page_content, vector))
        except UniqueViolation as exc:
            raise AppError(409, "document_exists", "同名文档已存在，本课不自动覆盖。") from exc
        return row

    def delete(self, did):
        with open_db() as conn:
            row = conn.execute("DELETE FROM study.api_documents WHERE id=%s RETURNING id",
                               (did,)).fetchone()
            if row is None:
                raise AppError(404, "document_not_found", "文档不存在。")

    def chunks(self):
        with open_db() as conn:
            return conn.execute("""SELECT d.id AS document_id,d.filename,d.embedding_model,
                c.chunk_id,c.content FROM study.api_document_chunks c
                JOIN study.api_documents d ON d.id=c.document_id
                ORDER BY d.id,c.chunk_id""").fetchall()

    def vector_hits(self, encoded, model, limit=5):
        with open_db() as conn:
            rows = conn.execute("""SELECT d.id AS document_id,d.filename,c.chunk_id,c.content,
                1-(c.embedding <=> CAST(%s AS vector)) AS score
                FROM study.api_document_chunks c
                JOIN study.api_documents d ON d.id=c.document_id
                WHERE d.embedding_model=%s
                ORDER BY c.embedding <=> CAST(%s AS vector),d.id,c.chunk_id LIMIT %s""",
                (encoded, model, encoded, limit)).fetchall()
        return [(float(row["score"]), to_document(row)) for row in rows]
