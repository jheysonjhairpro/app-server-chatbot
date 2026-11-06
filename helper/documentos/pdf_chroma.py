"""CRUD de la colección Chroma usada por el chatbot de PDF."""
from collections import defaultdict
import hashlib

import chromadb
import ollama

from configuracion import servidor as config
from compartido.documentos import ConflictError


def collection():
    client = chromadb.PersistentClient(path=str(config.CHROMA_DIR), settings=chromadb.Settings(anonymized_telemetry=False))
    return client.get_collection(config.COLECCION_DOCS)


def sources():
    db = collection()
    grouped = defaultdict(lambda: {'chunks': 0, 'pages': 0, 'title': ''})
    for offset in range(0, db.count(), 500):
        rows = db.get(limit=500, offset=offset, include=['metadatas'])
        for meta in rows['metadatas']:
            source = meta.get('source') or meta.get('file_name') or 'Sin origen'
            item = grouped[source]
            item['chunks'] += 1
            item['pages'] = max(item['pages'], int(meta.get('total_pages') or meta.get('page') or 0))
            item['title'] = meta.get('document_title') or item['title'] or source
    return {'sources': [{'source': key, **value} for key, value in sorted(grouped.items())],
            'total_chunks': db.count(), 'collection': config.COLECCION_DOCS}


def chunks(source):
    if not source:
        raise ValueError('Selecciona un PDF.')
    rows = collection().get(where={'source': source}, include=['documents', 'metadatas'])
    items = [{'id': identity, 'text': text, 'metadata': meta,
              'version': hashlib.sha256(text.encode('utf-8')).hexdigest()}
             for identity, text, meta in zip(rows['ids'], rows['documents'], rows['metadatas'])]
    items.sort(key=lambda item: (item['metadata'].get('page', 0), item['metadata'].get('chunk', 0)))
    return {'items': items, 'source': source}


def update_chunk(identity, text, version):
    if not isinstance(identity, str) or not identity or not isinstance(text, str) or not text.strip() or len(text) > 20000:
        raise ValueError('Fragmento no válido o demasiado largo.')
    db = collection()
    previous = db.get(ids=[identity], include=['documents', 'metadatas'])
    if not previous['ids']:
        raise FileNotFoundError('El fragmento ya no existe.')
    old = previous['documents'][0]
    if hashlib.sha256(old.encode('utf-8')).hexdigest() != version:
        raise ConflictError('El fragmento cambió. Recarga antes de guardar.')
    model = previous['metadatas'][0].get('embedding_model') or config.EMBEDDING_MODEL
    vector = ollama.Client(host=config.OLLAMA_HOST).embed(model=model, input=text, truncate=False).embeddings[0]
    db.update(ids=[identity], documents=[text], embeddings=[vector])
    return {'id': identity, 'text': text, 'version': hashlib.sha256(text.encode('utf-8')).hexdigest()}


def delete_chunk(identity, version):
    db = collection()
    previous = db.get(ids=[identity], include=['documents'])
    if not previous['ids']:
        raise FileNotFoundError('El fragmento ya no existe.')
    if hashlib.sha256(previous['documents'][0].encode('utf-8')).hexdigest() != version:
        raise ConflictError('El fragmento cambió. Recarga antes de eliminar.')
    db.delete(ids=[identity])
    return {'deleted': 1}


def delete_source(source):
    if not source:
        raise ValueError('Selecciona un PDF.')
    db = collection()
    ids = db.get(where={'source': source}, include=[])['ids']
    for start in range(0, len(ids), 500):
        db.delete(ids=ids[start:start + 500])
    return {'deleted': len(ids)}
