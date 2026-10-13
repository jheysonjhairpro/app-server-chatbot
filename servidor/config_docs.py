"""Valores del modo documentos; conserva los defaults anteriores.

Los argumentos propios de consultar_pdfs.py siguen siendo la fuente de su
configuración de ejecución. Este módulo no cambia esos argumentos.
"""
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

# Ollama
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
EMBEDDING_MODEL = os.environ.get("UNAMBA_EMBEDDING_MODEL", "qwen3-embedding:0.6b")
CHAT_MODEL = os.environ.get("UNAMBA_CHAT_MODEL", "qwen2.5:7b")
OLLAMA_KEEP_ALIVE = os.environ.get("UNAMBA_KEEP_ALIVE", "30m")

# Índice del motor de documentos.
CHROMA_DIR = BASE / "chroma_db"
COLECCION_DOCS = "documentos_unamba_qwen3"   # Normativas / PDF
