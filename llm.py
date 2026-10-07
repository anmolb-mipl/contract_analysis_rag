import json

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MODEL, MAX_AUDIT_CHARS
from prompts import AUDIT_SYSTEM, QA_SYSTEM
from schemas import Audit

_client: genai.Client | None = None


def _gemini() -> genai.Client:
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not set (see .env.example)")
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def chat_json(system: str, user: str, retries: int = 1) -> dict:
    """Call Gemini in JSON mode and return the parsed dict (retry once on bad JSON)."""
    last = ""
    for _ in range(retries + 1):
        resp = _gemini().models.generate_content(
            model=GEMINI_MODEL,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=0,
                response_mime_type="application/json",
            ),
        )
        last = resp.text or ""
        try:
            data = json.loads(last)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    raise ValueError(f"Model did not return a valid JSON object: {last[:200]}")


# ---------- Step 1: legality / completeness audit ----------
def build_audit_text(pages: list[dict], limit: int = MAX_AUDIT_CHARS) -> str:
    """Whole document if it fits; otherwise head + sampled middle + tail (signature page)."""
    full = "\n\n".join(f"[Page {p['page']}]\n{p['text']}" for p in pages)
    if len(full) <= limit:
        return full

    head = full[: int(limit * 0.4)]
    tail = full[-int(limit * 0.3) :]
    middle = full[len(head) : len(full) - len(tail)]
    budget = limit - len(head) - len(tail)
    slice_len = 600
    n = max(1, budget // slice_len)
    step = max(1, len(middle) // n)
    samples = [middle[i : i + slice_len] for i in range(0, len(middle), step)][:n]
    return f"{head}\n[...]\n" + "\n[...]\n".join(samples) + f"\n[...]\n{tail}"


def audit_document(pages: list[dict]) -> Audit:
    text = build_audit_text(pages)
    data = chat_json(AUDIT_SYSTEM, f"<document>\n{text}\n</document>\n\nReturn the JSON audit.")
    return Audit(**data)


# ---------- Step 2: question answering ----------
def answer_question(question: str, hits: list[dict]) -> dict:
    excerpts = "\n\n".join(
        f"[{h['id']}] (page {h['page']}, paragraph {h['para_start']})\n{h['text']}"
        for h in hits
    )
    user = (
        f"<excerpts>\n{excerpts}\n</excerpts>\n\n"
        f"Question: {question}\n\nReturn the JSON answer."
    )
    data = chat_json(QA_SYSTEM, user)
    valid_ids = {h["id"] for h in hits}
    # Only keep source ids that were really retrieved, so page numbers can't be invented.
    data["source_ids"] = [s for s in data.get("source_ids", []) if s in valid_ids]
    data.setdefault("answer", "")
    data["found"] = bool(data.get("found", False))
    return data
