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
