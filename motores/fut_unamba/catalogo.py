"""Tipos de FUT en una colección Chroma independiente."""
import json
from pathlib import Path

import chromadb
import ollama

from configuracion import servidor as config

CATALOG_PATH = Path(__file__).with_name('catalogo.json')


def entries():
    rows = json.loads(CATALOG_PATH.read_text(encoding='utf-8'))
    if not isinstance(rows, list) or any(not isinstance(row, dict) or not row.get('id') or not row.get('tipo') for row in rows):
        raise ValueError('El catálogo FUT no es válido.')
    return rows


def collection():
    config.FUT_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(config.FUT_DIR), settings=chromadb.Settings(anonymized_telemetry=False))
    return client.get_or_create_collection(config.FUT_COLLECTION, metadata={'embedding_model': config.EMBEDDING_MODEL, 'purpose': 'tipos_y_encargados_fut'})


def sync():
    """Indexa el catálogo editable; no almacena datos de estudiantes."""
    db = collection()
    rows = entries()
    current = db.get(include=['metadatas'])
    old = {identity: meta for identity, meta in zip(current['ids'], current['metadatas'])}
    wanted = set()
    model = ollama.Client(host=config.OLLAMA_HOST)
    for row in rows:
        identity = row['id']
        wanted.add(identity)
        document = f"{row['tipo']}. {row['descripcion']} Responsable de referencia: {row.get('encargado') or 'por confirmar'}. Cargo: {row.get('cargo') or 'por confirmar'}."
        metadata = {key: value for key, value in row.items() if isinstance(value, (str, int, float, bool))}
        metadata['document'] = document
        metadata['kind'] = 'tipo'
        if old.get(identity) == metadata:
            continue
        vector = model.embed(model=config.EMBEDDING_MODEL, input=document, truncate=False).embeddings[0]
        db.upsert(ids=[identity], documents=[document], metadatas=[metadata], embeddings=[vector])
    obsolete = [identity for identity in current['ids'] if not identity.startswith('funcionario:') and identity not in wanted]
    if obsolete:
        db.delete(ids=obsolete)
    return {'collection': db.name, 'count': db.count(), 'path': str(config.FUT_DIR)}


def best_match(text):
    db = collection()
    sync()
    vector = ollama.Client(host=config.OLLAMA_HOST).embed(model=config.EMBEDDING_MODEL, input=text, truncate=False).embeddings[0]
    result = db.query(query_embeddings=[vector], n_results=min(3, len(entries())), where={'kind': 'tipo'}, include=['metadatas', 'distances'])
    return list(zip(result['metadatas'][0], result['distances'][0]))


if __name__ == '__main__':
    print(json.dumps(sync(), ensure_ascii=False))
