"""Configuración central. Todo lo que se cambia con frecuencia vive aquí."""
import os
from pathlib import Path
from configuracion.entorno import ruta, booleano

BASE = Path(__file__).resolve().parent.parent


# TOKEN
TOKEN = os.getenv('UNAMBA_TOKEN', '').strip()
if not TOKEN:
    raise ValueError('Falta UNAMBA_TOKEN en .env. Ejecuta iniciar.bat para preparar la configuración.')

HOST = "127.0.0.1"
PORT = int(os.environ.get("UNAMBA_PORT", "5000"))

# Ollama
OLLAMA_HOST = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
EMBEDDING_MODEL = os.environ.get("UNAMBA_EMBEDDING_MODEL", "qwen3-embedding:0.6b")
CHAT_MODEL = os.environ.get("UNAMBA_CHAT_MODEL", "qwen2.5:7b")
OLLAMA_KEEP_ALIVE = os.environ.get("UNAMBA_KEEP_ALIVE", "30m")

# Base de datos vectorial: una colección por modo.
CHROMA_DIR = ruta("PDF_INDEX_DIR", "base de datos/pdf")
COLECCION_DOCS = os.getenv("PDF_COLLECTION", "documentos_unamba_qwen3")
# Las consultas funcionan sin Internet; la búsqueda en gob.pe es opcional.
PDF_BUSCAR_ENLINEA = booleano("PDF_BUSCAR_ENLINEA", False)

OCR_MODEL = os.getenv("PDF_OCR_MODEL", "seriouswebby/paddleocr-vl-1.6:latest")
PDF_ORIGINALS_DIR = ruta("PDF_ORIGINALS_DIR", "datos/pdf/originales")
PDF_TEXT_DIR = ruta("PDF_TEXT_DIR", "datos/pdf/texto_extraido")
PDF_POPPLER_DIR = ruta("PDF_POPPLER_DIR", "herramientas/poppler/Library/bin")
