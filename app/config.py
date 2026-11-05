"""Central configuration. Every value can be overridden with an environment variable or the
project's .env file, so nothing is hard-coded in the modules."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")      # real environment variables win

PROJECT_ROOT = Path(os.getenv("LMS_PROJECT_ROOT", Path(__file__).resolve().parents[1]))

COURSES_DIR = PROJECT_ROOT / os.getenv("LMS_COURSES_DIR", "Courses")
DATA_DIR = PROJECT_ROOT / "data"
EXTRACTED_TEXT_DIR = DATA_DIR / "extracted_text"
PROCESSED_DIR = DATA_DIR / "processed"
RULES_PATH = PROJECT_ROOT / os.getenv("LMS_RULES_FILE", "config/ingestion_rules.json")

# --- Chunking (token counts are *estimates*, see chunker.estimate_tokens) -------------
# all-MiniLM-L6-v2 only reads the first 256 tokens of an input, so chunks must stay below that.
CHUNK_MAX_TOKENS = int(os.getenv("CHUNK_MAX_TOKENS", 220))
CHUNK_OVERLAP_TOKENS = int(os.getenv("CHUNK_OVERLAP_TOKENS", 30))   # prose only, never lists

# A page with fewer extracted characters than this is reported as "little or no text".
LOW_TEXT_CHAR_THRESHOLD = int(os.getenv("LOW_TEXT_CHAR_THRESHOLD", 40))

# --- Embeddings and vector database ---------------------------------------------------
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
EMBEDDING_BATCH_SIZE = int(os.getenv("EMBEDDING_BATCH_SIZE", 16))   # small: runs on a laptop CPU
CHROMA_DIR = PROJECT_ROOT / os.getenv("CHROMA_DIR", "vector_db/chroma")
CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION", "course_chunks")

# --- Retrieval ------------------------------------------------------------------------
RETRIEVAL_RULES_PATH = PROJECT_ROOT / os.getenv("LMS_RETRIEVAL_RULES_FILE", "config/retrieval_rules.json")
RETRIEVAL_TOP_K = int(os.getenv("RETRIEVAL_TOP_K", 4))
# Below this similarity a question is treated as unrelated to the courses.
RETRIEVAL_MIN_SIMILARITY = float(os.getenv("RETRIEVAL_MIN_SIMILARITY", 0.30))
# Stricter when no course is named. Measured on the dev questions: the highest off-topic score
# was 0.409 and the lowest real one 0.482. Re-measure this with the real PDFs.
RETRIEVAL_MIN_SIMILARITY_NO_COURSE = float(os.getenv("RETRIEVAL_MIN_SIMILARITY_NO_COURSE", 0.445))
# A follow-up names no course. It needs less than the no-course cutoff, because "What about the
# price?" scored 0.414, but more than a named course: "the price of bitcoin..." scored 0.383.
RETRIEVAL_MIN_SIMILARITY_FOLLOW_UP = float(os.getenv("RETRIEVAL_MIN_SIMILARITY_FOLLOW_UP", 0.40))
# It also passes if it sounds like an example question, or has "it" in this few words.
RETRIEVAL_FOLLOW_UP_MIN_EXAMPLE = float(os.getenv("RETRIEVAL_FOLLOW_UP_MIN_EXAMPLE", 0.50))
RETRIEVAL_FOLLOW_UP_MAX_WORDS = int(os.getenv("RETRIEVAL_FOLLOW_UP_MAX_WORDS", 5))
# A question no pattern recognises takes a type from the example questions only if its best
# example scores at least this, and beats the next type by at least the margin.
RETRIEVAL_MIN_INTENT_SIMILARITY = float(os.getenv("RETRIEVAL_MIN_INTENT_SIMILARITY", 0.55))
RETRIEVAL_MIN_INTENT_MARGIN = float(os.getenv("RETRIEVAL_MIN_INTENT_MARGIN", 0.03))

# --- Answer generation ----------------------------------------------------------------
# Gemini's free tier is for the demo only: see app/generation/llm.py before using real PDFs.
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")      # gemini or ollama
# The local model. To change it: ollama pull <model>, then set LLM_MODEL. No code change is needed.
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3:1.7b")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:11434")
# Prompt plus answer must fit. A typical prompt is about 700 tokens, but outlines of several courses are
# far larger, and those are refused rather than cut. See OllamaGenerator.
NUM_CTX = int(os.getenv("NUM_CTX", 4096))
LLM_NUM_PREDICT = int(os.getenv("LLM_NUM_PREDICT", 512))    # answer limit; a reply that reaches it is an error
LLM_KEEP_ALIVE = os.getenv("LLM_KEEP_ALIVE", "30m")         # stay loaded, because loading adds seconds to a reply
OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", 180))   # a CPU is far slower than Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_THINKING_LEVEL = os.getenv("GEMINI_THINKING_LEVEL", "low")   # 3.8 Flash: low, medium or high
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", 0.2))
LLM_MAX_OUTPUT_TOKENS = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", 4096))   # includes the model's thinking
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", 30))
MAX_QUESTION_CHARS = int(os.getenv("MAX_QUESTION_CHARS", 500))

# --- REST API -------------------------------------------------------------------------
# Only this machine by default: there is no API key yet, so do not open it to the network.
API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", 8000))
CHAT_MAX_SESSIONS = int(os.getenv("CHAT_MAX_SESSIONS", 200))             # above this, the oldest goes
CHAT_SESSION_TTL_MINUTES = float(os.getenv("CHAT_SESSION_TTL_MINUTES", 60))   # idle time before a session expires
# Below the page's own limit of 190 s, so the student gets the "busy" message, not a timeout.
CHAT_QUEUE_WAIT_SECONDS = float(os.getenv("CHAT_QUEUE_WAIT_SECONDS", 170))
CHAT_STARTUP_WAIT_SECONDS = float(os.getenv("CHAT_STARTUP_WAIT_SECONDS", 60))  # a question this early waits, then gets a 503
HEALTH_TIMEOUT_SECONDS = float(os.getenv("HEALTH_TIMEOUT_SECONDS", 3))   # how long /health waits for Ollama
