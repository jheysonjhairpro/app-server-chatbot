"""Importación de PDF y lectura de los fragmentos publicados."""
import re
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from motores.portal_unamba.src.config import CHROMA_COLLECTION, VECTOR_STORE_DIR
from configuracion import servidor as pdf_config

MAX_PDF = 25 * 1024 * 1024


def import_pdf(store, filename, data):
    if not isinstance(filename, str) or not re.fullmatch(r'[^/\\:<>|?*\x00-\x1f]+\.pdf', filename, re.I):
        raise ValueError('Nombre de PDF no válido.')
    if len(data) > MAX_PDF or not data.startswith(b'%PDF-'):
        raise ValueError('El PDF no es válido o supera 25 MB.')
    try:
        reader = PdfReader(BytesIO(data))
    except PdfReadError as exc:
        raise ValueError('No se pudo leer el PDF.') from exc
    if reader.is_encrypted:
        raise ValueError('El PDF está protegido con contraseña.')
    if len(reader.pages) > 500:
        raise ValueError('El PDF supera 500 páginas.')
    pages = [(page.extract_text() or '').strip() for page in reader.pages]
    if not any(pages):
        raise ValueError('No se encontró texto seleccionable. Este PDF requiere OCR antes de importarlo.')
    original = pdf_config.PDF_ORIGINALS_DIR / filename
    if original.exists():
        from compartido.documentos import ConflictError
        raise ConflictError('Ya existe un PDF con ese nombre. Renómbralo antes de importarlo.')
    original.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(data)
    return {'source': filename, 'pages': len(pages), 'pages_with_text': sum(bool(p) for p in pages)}


def fragments(path=None):
    import chromadb
    client = chromadb.PersistentClient(path=str(VECTOR_STORE_DIR), settings=chromadb.Settings(anonymized_telemetry=False))
    try:
        collection = client.get_collection(CHROMA_COLLECTION)
    except Exception:
        return {'items': [], 'total': 0}
    options = {'include': ['documents', 'metadatas'], 'limit': 1000}
    if path:
        options['where'] = {'source_file': path}
    result = collection.get(**options)
    items = [{'id': key, 'text': text, 'metadata': meta or {}}
             for key, text, meta in zip(result['ids'], result['documents'], result['metadatas'])]
    items.sort(key=lambda item: item['metadata'].get('chunk_index', 0))
    return {'items': items, 'total': len(items)}
