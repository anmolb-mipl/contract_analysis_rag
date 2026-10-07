import re
import uuid

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from google.genai import errors as genai_errors
from pydantic import ValidationError

import llm
import store
from config import CHUNK_MAX_CHARS, MAX_UPLOAD_MB
from pdf_parser import chunk_pages, extract_pages, looks_scanned
from schemas import AskRequest, AskResponse, Audit, Citation

app = FastAPI(title="Contract RAG", version="0.1.0")

DISCLAIMER = "This is not legal advice. Please consult a qualified lawyer before signing."
_ID_RE = re.compile(r"^[0-9a-f]{32}$")


# ---------- error handling for the LLM layer ----------
@app.exception_handler(genai_errors.APIError)
async def _gemini_error(_, exc):
    if getattr(exc, "code", None) == 429:
        return JSONResponse(status_code=429, content={"detail": "Gemini rate limit reached. Retry in a few seconds."})
    return JSONResponse(status_code=502, content={"detail": f"Gemini API error: {exc}"})


@app.exception_handler(RuntimeError)
async def _config_error(_, exc):
    return JSONResponse(status_code=500, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def _bad_llm_output(_, exc):
    return JSONResponse(status_code=502, content={"detail": f"LLM returned unusable output: {exc}"})


@app.exception_handler(ValidationError)
async def _bad_schema(_, exc):
    return JSONResponse(status_code=502, content={"detail": "LLM output did not match the expected format."})


# ---------- helpers ----------
def _get_doc(doc_id: str) -> dict:
    if not _ID_RE.match(doc_id):
        raise HTTPException(404, "Document not found")
    meta = store.load_meta(doc_id)
    if not meta:
        raise HTTPException(404, "Document not found")
    return meta


# ---------- endpoints ----------
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/documents")
def upload_document(file: UploadFile = File(...)):
    """Upload a PDF -> parse -> legality audit -> (if safe) index for Q&A."""
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported.")

    data = file.file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File too large (max {MAX_UPLOAD_MB} MB).")

    try:
        pages = extract_pages(data)
    except Exception:
        raise HTTPException(400, "Could not read this PDF (corrupt or password-protected).")

    if not pages or looks_scanned(pages):
        raise HTTPException(
            422,
            "This PDF looks scanned (no selectable text). OCR is not supported yet; upload a text-based PDF.",
        )

    audit: Audit = llm.audit_document(pages)

    doc_id = uuid.uuid4().hex
    indexed = False
    if audit.is_safe_to_proceed:
        store.add_chunks(doc_id, chunk_pages(pages, CHUNK_MAX_CHARS))
        indexed = True

    store.save_meta(
        doc_id,
        {
            "filename": file.filename,
            "num_pages": len(pages),
            "indexed": indexed,
            "audit": audit.model_dump(),
        },
    )
    return {
        "doc_id": doc_id,
        "filename": file.filename,
        "num_pages": len(pages),
        "ready_for_questions": indexed,
        "audit": audit,
        "disclaimer": DISCLAIMER,
    }


@app.get("/documents/{doc_id}/audit")
def get_audit(doc_id: str):
    meta = _get_doc(doc_id)
    return {"doc_id": doc_id, "audit": meta["audit"], "disclaimer": DISCLAIMER}


@app.post("/documents/{doc_id}/ask", response_model=AskResponse)
def ask(doc_id: str, body: AskRequest):
    meta = _get_doc(doc_id)
    if not meta["indexed"]:
        raise HTTPException(
            409,
            {
                "message": "Questions are disabled because the document failed the legality check.",
                "status": meta["audit"]["status"],
                "reasoning_summary": meta["audit"]["reasoning_summary"],
            },
        )

    hits = store.search(doc_id, body.question, body.top_k)
    result = llm.answer_question(body.question, hits)

    by_id = {h["id"]: h for h in hits}
    citations = []
    for sid in result["source_ids"]:
        h = by_id[sid]
        a, b = h["para_start"], h["para_end"]
        citations.append(
            Citation(
                chunk_id=sid,
                page=h["page"],
                paragraph=str(a) if a == b else f"{a}-{b}",
                excerpt=h["text"][:300],
            )
        )

    return AskResponse(answer=result["answer"], found=result["found"], citations=citations)


@app.delete("/documents/{doc_id}")
def delete_document(doc_id: str):
    _get_doc(doc_id)
    store.delete_doc(doc_id)
    return {"deleted": doc_id}
