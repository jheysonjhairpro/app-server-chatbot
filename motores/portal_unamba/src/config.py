"""Configuración central del proyecto, sobreescribible mediante variables de entorno (.env)."""

import os
from pathlib import Path

from configuracion.entorno import ruta

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# Carga la configuración compartida sin depender del directorio de inicio.
APP_ROOT = PROJECT_ROOT.parents[1]



def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "si", "sí"}


def _int(name: str, default: int, legacy: str | None = None) -> int:
    return int(os.getenv(name) or (os.getenv(legacy) if legacy else None) or default)


def _float(name: str, default: float) -> float:
    return float(os.getenv(name) or default)


DATA_DIR = APP_ROOT / "datos" / "portal"
RAW_DATA_DIR = ruta("RAG_RAW_DIR", DATA_DIR / "documentos")
VECTOR_STORE_DIR = ruta("RAG_VECTOR_STORE_DIR", APP_ROOT / "base de datos" / "portal")
LOGS_DIR = ruta("RAG_LOGS_DIR", APP_ROOT / "registros" / "portal")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
LLM_MODEL = os.getenv("RAG_LLM_MODEL", "qwen2.5:7b")
EMBED_MODEL = os.getenv("RAG_EMBED_MODEL", "qwen3-embedding:0.6b")
TEMPERATURE = _float("RAG_TEMPERATURE", 0.0)
OLLAMA_REQUEST_TIMEOUT = _float("OLLAMA_REQUEST_TIMEOUT", 300)
OLLAMA_CONTEXT_SIZE = _int("OLLAMA_CONTEXT_SIZE", 8192)
NUM_PREDICT = _int("RAG_NUM_PREDICT", 1024)

CHROMA_COLLECTION = os.getenv("RAG_CHROMA_COLLECTION", "crawl4ai_documentos")
CHUNK_SIZE = _int("RAG_CHUNK_SIZE", 512)
CHUNK_OVERLAP = _int("RAG_CHUNK_OVERLAP", 50)

# --- Recuperación ---
SIMILARITY_TOP_K = _int("RAG_SIMILARITY_TOP_K", 20)          # candidatos vectoriales
BM25_TOP_K = _int("RAG_BM25_TOP_K", 20)                      # candidatos léxicos
# Fragmentos finales enviados al LLM (acepta el nombre antiguo RAG_RERANK_CANDIDATES).
CONTEXT_TOP_N = _int("RAG_CONTEXT_TOP_N", 6, legacy="RAG_RERANK_CANDIDATES")
ENABLE_HYBRID = _bool("RAG_ENABLE_HYBRID", True)             # vectorial + BM25 (siglas, fechas, nombres)
# Compatibilidad con .env antiguos: los umbrales siguientes ya no descartan candidatos.
MIN_SIMILARITY_SCORE = _float("RAG_MIN_SIMILARITY_SCORE", 0.20)
VECTOR_SCORE_RATIO = _float("RAG_VECTOR_SCORE_RATIO", 0.80)  # descarta candidatos alejados del mejor
MIN_BM25_SCORE = _float("RAG_MIN_BM25_SCORE", 3.0)
ENABLE_RERANKER = _bool("RAG_ENABLE_RERANKER", False)
RERANK_MODEL = os.getenv("RAG_RERANK_MODEL", "BAAI/bge-reranker-base")
MIN_RERANK_SCORE = _float("RAG_MIN_RERANK_SCORE", 0.0)       # 0 = sin umbral
# Qwen3-Embedding rinde mejor con una instrucción en la consulta. Vacío = desactivada.
QUERY_INSTRUCTION = os.getenv(
    "RAG_QUERY_INSTRUCTION",
    "Given a question about the Universidad Nacional Micaela Bastidas de Apurímac (UNAMBA), "
    "retrieve passages that answer it" if "qwen3-embedding" in EMBED_MODEL else "",
)

# --- Conversación y registros ---
HISTORY_TURNS = _int("RAG_HISTORY_TURNS", 3)                 # 0 = sin memoria de conversación
LOG_UNANSWERED = _bool("RAG_LOG_UNANSWERED", True)           # guarda preguntas sin respuesta en logs/

# Recuperación general: conserva el corpus; controla solo el contexto por consulta.
ENABLE_QUERY_PLANNING = _bool("RAG_ENABLE_QUERY_PLANNING", False)
REVIEW_LISTS = _bool("RAG_REVIEW_LISTS", True)
MAX_CONTEXT_TOKENS = _int("RAG_MAX_CONTEXT_TOKENS", 0)
CONTEXT_NEIGHBORS = _int("RAG_CONTEXT_NEIGHBORS", 1)
LOG_RETRIEVAL = _bool("RAG_LOG_RETRIEVAL", True)

# Perfil local para público general: menos llamadas, conservando validación.
FAST_MODE = _bool('RAG_FAST_MODE', True)
ANSWER_CACHE_SIZE = max(0, _int('RAG_ANSWER_CACHE_SIZE', 128))
ANSWER_CACHE_TTL = max(0, _int('RAG_ANSWER_CACHE_TTL', 300))
STREAM_RESPONSES = _bool('RAG_STREAM_RESPONSES', True)

ADMIN_DIR = ruta("RAG_ADMIN_DIR", DATA_DIR / "admin")
