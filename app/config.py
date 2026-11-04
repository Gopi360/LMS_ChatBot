"""Central configuration. Every value can be overridden with an environment variable
(or a .env file loaded by your shell/IDE) so nothing is hard-coded in the modules."""
import os
from pathlib import Path

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
# A question no pattern recognises takes a type from the example questions only if its best
# example scores at least this, and beats the next type by at least the margin.
RETRIEVAL_MIN_INTENT_SIMILARITY = float(os.getenv("RETRIEVAL_MIN_INTENT_SIMILARITY", 0.55))
RETRIEVAL_MIN_INTENT_MARGIN = float(os.getenv("RETRIEVAL_MIN_INTENT_MARGIN", 0.03))
