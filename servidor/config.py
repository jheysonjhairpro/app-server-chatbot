"""Configuración del servidor HTTP compartido.

El motor web carga motores/chatbot_web/config.py y su .env.
Las opciones del antiguo motor web fueron retiradas: no tenían consumidores.
"""
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(BASE / "motores"))

# TOKEN
TOKEN = os.environ.get("UNAMBA_TOKEN", "unamba2026-Xk9Pq7Lm3Rt8Vw2Zc5")

HOST = "0.0.0.0"
PORT = 5000

# Compatibilidad para consumidores anteriores de la configuración PDF.
from config_docs import (
    CHAT_MODEL,
    CHROMA_DIR,
    COLECCION_DOCS,
    EMBEDDING_MODEL,
    OLLAMA_HOST,
    OLLAMA_KEEP_ALIVE,
)
