"""Configuración central. Todo lo que se cambia con frecuencia vive aquí."""
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(BASE / "motores"))

# TOKEN
TOKEN = os.environ.get("UNAMBA_TOKEN", "unamba2026-Xk9Pq7Lm3Rt8Vw2Zc5")

HOST = "0.0.0.0"
PORT = 5000

# Ollama
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
EMBEDDING_MODEL = os.environ.get("UNAMBA_EMBEDDING_MODEL", "qwen3-embedding:0.6b")
CHAT_MODEL = os.environ.get("UNAMBA_CHAT_MODEL", "qwen2.5:7b")
OLLAMA_KEEP_ALIVE = os.environ.get("UNAMBA_KEEP_ALIVE", "30m")

# Base de datos vectorial: una colección por modo.
CHROMA_DIR = BASE / "chroma_db"
COLECCION_DOCS = "documentos_unamba_qwen3"   # Normativas / PDF
COLECCION_WEB = "web_unamba_qwen3"            # Portal Web

# RAG web
WEB_ALLOWED_DOMAINS = [
    "admision.unamba.edu.pe",
    "biblioteca.unamba.edu.pe",
    "centrodeinformatica.unamba.edu.pe",
    "comedor.unamba.edu.pe",
    "cpu.unamba.edu.pe",
    "idiomas.unamba.edu.pe",
    "sisacademico.unamba.edu.pe",
    "tramite.unamba.edu.pe",
    "unamba.edu.pe",
]
WEB_CHUNK_CHARS = int(os.environ.get("UNAMBA_WEB_CHUNK_CHARS", "1800"))
WEB_CHUNK_OVERLAP = int(os.environ.get("UNAMBA_WEB_CHUNK_OVERLAP", "250"))
WEB_TOP_K = int(os.environ.get("UNAMBA_WEB_TOP_K", "12"))
WEB_MAX_CONTEXT_CHARS = int(os.environ.get("UNAMBA_WEB_MAX_CONTEXT_CHARS", "14000"))
WEB_CRAWL_MAX_PAGES = int(os.environ.get("UNAMBA_WEB_CRAWL_MAX_PAGES", "300"))
WEB_CRAWL_MAX_DEPTH = int(os.environ.get("UNAMBA_WEB_CRAWL_MAX_DEPTH", "2"))
WEB_MIN_TEXT_CHARS = int(os.environ.get("UNAMBA_WEB_MIN_TEXT_CHARS", "350"))
