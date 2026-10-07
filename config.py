import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# The google-genai SDK accepts either name; GEMINI_API_KEY is preferred.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
# Model names change over time; override in .env if this one is retired.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")

DATA_DIR = Path(os.getenv("DATA_DIR", "./data"))
DOCS_DIR = DATA_DIR / "docs"
DOCS_DIR.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_MB = 15
# Gemini has a large context window, so the audit can see much more than with Groq.
MAX_AUDIT_CHARS = int(os.getenv("MAX_AUDIT_CHARS", "60000"))
CHUNK_MAX_CHARS = 900
TOP_K = 6
