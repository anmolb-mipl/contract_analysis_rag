"""Streamlit front end for the Contract RAG API (FastAPI backend must be running)."""
import os

import requests
import streamlit as st

st.set_page_config(page_title="Contract Q&A", page_icon="📄", layout="wide")

DEFAULT_API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")

SUGGESTIONS = [
    "Who are the parties in this contract?",
    "What is the amount, and where is it mentioned?",
    "What are the termination or cancellation terms?",
    "Are there any penalties or fines?",
]

# status -> (streamlit message type, plain-language label)
STATUS_UI = {
    "VALID": ("success", "Looks like a valid contract"),
    "INCOMPLETE": ("warning", "Looks like a contract, but parts are missing"),
    "ILLEGAL": ("error", "Blocked: this document appears to be illegal or a scam"),
    "NOT_A_CONTRACT": ("error", "Blocked: this document is not a contract"),
}

ss = st.session_state
ss.setdefault("docs", {})    # doc_id -> {filename, num_pages, ready, audit}
ss.setdefault("chats", {})   # doc_id -> list of messages


# ---------------------------------------------------------------- helpers
def md(text) -> str:
    """Escape $ so amounts like $5,000 are not rendered as LaTeX math."""
    return str(text).replace("$", r"\$")


def _error_text(r: requests.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return f"Error {r.status_code}: {r.text[:200]}"
    detail = body.get("detail", r.text) if isinstance(body, dict) else r.text
    if isinstance(detail, dict):  # e.g. 409 blocked document
        msg = detail.get("message", "Request refused.")
        reason = detail.get("reasoning_summary")
        return f"{msg} {reason}" if reason else msg
    if isinstance(detail, list):  # FastAPI validation errors
        return "; ".join(str(d.get("msg", d)) if isinstance(d, dict) else str(d) for d in detail)
    return str(detail)


def api(method: str, path: str, timeout: int = 60, **kwargs):
    """Call the backend. Returns (json, None) on success or (None, message) on failure."""
    url = ss.api_url.rstrip("/") + path
    try:
        r = requests.request(method, url, timeout=timeout, **kwargs)
    except requests.ConnectionError:
        return None, f"Cannot reach the API at {ss.api_url}. Is uvicorn running?"
    except requests.Timeout:
        return None, "The API took too long to answer. Please try again."
    if r.ok:
        return r.json(), None
    return None, _error_text(r)


def delete_active():
    """Button callback: delete the selected document on the server and forget it here."""
    doc_id = ss.get("active")
    if not doc_id:
        return
    _, err = api("DELETE", f"/documents/{doc_id}")
    if err:
        ss["flash"] = ("error", err)
        return
    name = ss.docs.get(doc_id, {}).get("filename", "Document")
    ss.docs.pop(doc_id, None)
    ss.chats.pop(doc_id, None)
    ss.pop("active", None)
    ss["flash"] = ("success", f"Deleted {name}.")


def ask_suggestion(question: str):
    ss["pending_q"] = question


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Documents")
    st.text_input("API address", value=DEFAULT_API_URL, key="api_url")
    _, health_err = api("GET", "/health", timeout=3)
    st.markdown(":red[Not connected]" if health_err else ":green[Connected]")

    uploaded = st.file_uploader("Upload a contract (PDF)", type="pdf")
    if st.button("Check and upload", type="primary", disabled=uploaded is None):
        with st.spinner("Reading the document and checking it. This can take a minute..."):
            data, err = api(
                "POST",
                "/documents",
                timeout=300,
                files={"file": (uploaded.name, uploaded.getvalue(), "application/pdf")},
            )
        if err:
            st.error(err)
        else:
            ss.docs[data["doc_id"]] = {
                "filename": data["filename"],
                "num_pages": data["num_pages"],
                "ready": data["ready_for_questions"],
                "audit": data["audit"],
            }
            ss.chats[data["doc_id"]] = []
            ss["active"] = data["doc_id"]
            st.rerun()

    with st.expander("Open an earlier document"):
        open_id = st.text_input("Document ID", key="open_id")
        if st.button("Open", disabled=not open_id.strip()):
            rid = open_id.strip()
            data, err = api("GET", f"/documents/{rid}/audit")
            if err:
                st.error(err)
            else:
                ss.docs[rid] = {
                    "filename": f"Document {rid[:8]}",
                    "num_pages": None,
                    "ready": data["audit"].get("is_safe_to_proceed", False),
                    "audit": data["audit"],
                }
                ss.chats.setdefault(rid, [])
                ss["active"] = rid
                st.rerun()

    if ss.docs:
        ids = list(ss.docs)
        st.selectbox(
            "Current document",
            ids,
            key="active",
            format_func=lambda i: ss.docs[i]["filename"],
        )
        st.button("Delete this document", on_click=delete_active)

    st.divider()
    top_k = st.slider(
        "Passages per question",
        min_value=1,
        max_value=12,
        value=6,
        help="How many parts of the contract are read to answer each question. "
        "Raise it if answers say 'not found' but you know the information is in the file.",
    )
    st.caption("This tool is not legal advice. Please consult a qualified lawyer before signing.")


# ---------------------------------------------------------------- main area
st.title("Contract Q&A")

if "flash" in ss:
    kind, text = ss.pop("flash")
    getattr(st, kind)(text)

if not ss.docs or ss.get("active") not in ss.docs:
    st.info("Upload a contract PDF in the sidebar to check it and start asking questions.")
    st.stop()

doc_id = ss["active"]
doc = ss.docs[doc_id]
audit = doc["audit"]


def show_audit():
    kind, label = STATUS_UI.get(audit["status"], ("info", audit["status"]))
    getattr(st, kind)(label)

    c1, c2, c3 = st.columns(3)
    c1.markdown(f"**Type**  \n{md(audit.get('contract_type', 'Unknown'))}")
    c2.markdown(f"**Confidence**  \n{audit.get('confidence', 'medium').capitalize()}")
    c3.markdown(f"**Pages**  \n{doc.get('num_pages') or 'unknown'}")

    st.markdown(md(audit["reasoning_summary"]))

    missing = audit.get("missing_critical_elements") or []
    if missing:
        st.markdown("**Missing or unclear**")
        for item in missing:
            st.markdown(f"- {md(item)}")

    flags = audit.get("red_flags") or []
    if flags:
        with st.expander(f"Red flags ({len(flags)})"):
            for f in flags:
                where = f" (page {f['page']})" if f.get("page") else ""
                st.markdown(f"**{md(f['issue'])}**{where}")
                if f.get("quote"):
                    st.markdown("> " + md(f["quote"]))


with st.expander(f"Document check: {doc['filename']}", expanded=len(ss.chats.get(doc_id, [])) == 0):
    show_audit()


def show_sources(citations):
    if not citations:
        return
    with st.expander(f"Sources ({len(citations)})"):
        for c in citations:
            where = f"Page {c['page']}"
            if c.get("printed_page"):
                where += f" (printed page {c['printed_page']})"
            where += f", paragraph {c['paragraph']}"
            st.markdown(f"**{where}**")
            st.markdown("> " + md(c["excerpt"]))


def render_message(m):
    with st.chat_message(m["role"]):
        st.markdown(md(m["content"]))
        if m["role"] == "assistant":
            if m.get("found") is False:
                st.caption(
                    "Not found in the passages that were read. Try rephrasing, "
                    "or raise “Passages per question” in the sidebar."
                )
            show_sources(m.get("citations"))


# ---------------------------------------------------------------- chat
if not doc["ready"]:
    st.warning(
        "Questions are turned off for this document because it did not pass the check. "
        "Upload a different file to continue."
    )

history = ss.chats.setdefault(doc_id, [])
for m in history:
    render_message(m)

if doc["ready"] and not history:
    st.markdown("Try a question:")
    cols = st.columns(len(SUGGESTIONS))
    for col, q in zip(cols, SUGGESTIONS):
        col.button(q, key=f"sugg_{q}", on_click=ask_suggestion, args=(q,))

typed = st.chat_input(
    "Ask about this contract, e.g. where is the sale amount mentioned?",
    disabled=not doc["ready"],
)
prompt = typed or ss.pop("pending_q", None)

if prompt and doc["ready"]:
    user_msg = {"role": "user", "content": prompt}
    render_message(user_msg)
    with st.chat_message("assistant"):
        with st.spinner("Searching the contract..."):
            data, err = api(
                "POST",
                f"/documents/{doc_id}/ask",
                timeout=120,
                json={"question": prompt, "top_k": top_k},
            )
        if err:
            st.error(err)
        else:
            answer_msg = {
                "role": "assistant",
                "content": data["answer"],
                "found": data["found"],
                "citations": data["citations"],
            }
            history.append(user_msg)
            history.append(answer_msg)
            st.rerun()  # redraw from history so the layout stays consistent