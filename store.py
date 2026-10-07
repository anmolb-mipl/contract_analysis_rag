import json
from typing import Optional

import chromadb

from config import DATA_DIR, DOCS_DIR

# Uses Chroma's default local embedding model (all-MiniLM-L6-v2, downloaded on first use).
_client = chromadb.PersistentClient(path=str(DATA_DIR / "chroma"))


def _col_name(doc_id: str) -> str:
    return f"doc_{doc_id}"


# ---------- vectors ----------
def add_chunks(doc_id: str, chunks: list[dict]) -> None:
    col = _client.get_or_create_collection(
        _col_name(doc_id), metadata={"hnsw:space": "cosine"}
    )
    col.add(
        ids=[c["id"] for c in chunks],
        documents=[c["text"] for c in chunks],
        metadatas=[
            {"page": c["page"], "para_start": c["para_start"], "para_end": c["para_end"]}
            for c in chunks
        ],
    )


def search(doc_id: str, query: str, k: int) -> list[dict]:
    col = _client.get_collection(_col_name(doc_id))
    res = col.query(query_texts=[query], n_results=min(k, col.count()))
    hits = []
    for i, cid in enumerate(res["ids"][0]):
        meta = res["metadatas"][0][i]
        hits.append(
            {
                "id": cid,
                "text": res["documents"][0][i],
                "page": meta["page"],
                "para_start": meta["para_start"],
                "para_end": meta["para_end"],
                "distance": res["distances"][0][i],
            }
        )
    # Present excerpts in document order so the LLM reads them naturally.
    hits.sort(key=lambda h: (h["page"], h["para_start"]))
    return hits


# ---------- document metadata ----------
def save_meta(doc_id: str, meta: dict) -> None:
    (DOCS_DIR / f"{doc_id}.json").write_text(json.dumps(meta, indent=2))


def load_meta(doc_id: str) -> Optional[dict]:
    path = DOCS_DIR / f"{doc_id}.json"
    return json.loads(path.read_text()) if path.exists() else None


def delete_doc(doc_id: str) -> bool:
    path = DOCS_DIR / f"{doc_id}.json"
    existed = path.exists()
    path.unlink(missing_ok=True)
    try:
        _client.delete_collection(_col_name(doc_id))
    except Exception:
        pass
    return existed
