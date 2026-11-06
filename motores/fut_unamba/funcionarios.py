"""Indexa nombres y cargos publicados por UNAMBA en gob.pe y los consulta para FUT."""
import json
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urljoin

import ollama
import requests
from bs4 import BeautifulSoup

from configuracion import servidor as config
from motores.fut_unamba.catalogo import collection

URL = 'https://www.gob.pe/institucion/universidad-nacional-micaela-bastidas-de-apurimac/funcionarios'
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; UNAMBA-FUT/1.0)'}
STOP = {'el', 'la', 'de', 'del', 'para', 'un', 'una', 'al', 'a', 'y', 'que', 'quien',
        'encargado', 'encargada', 'responsable', 'jefe', 'jefa', 'director', 'directora',
        'universitario', 'universitaria', 'unidad', 'area', 'por', 'favor', 'es', 'nombre',
        'humm', 'verdad', 'podrias', 'puedes', 'decirme', 'decir', 'saber', 'conoces',
        'necesito', 'quiero', 'busco', 'persona', 'autoridad', 'dime', 'favor'}


def _plain(value):
    value = unicodedata.normalize('NFKD', value.casefold())
    return ''.join(char for char in value if not unicodedata.combining(char))


def _words(value):
    return {word for word in re.findall(r'[a-z0-9]+', _plain(value)) if word not in STOP and len(word) > 2}


def fetch_page(page, *, session=None):
    """Una página de la lista; no sigue enlaces de perfiles ni de otras instituciones."""
    if page < 1 or page > 4:
        raise ValueError('La lista oficial esperada tiene páginas 1 a 4.')
    response = (session or requests).get(URL, params={'sheet': page}, headers=HEADERS, timeout=25)
    response.raise_for_status()
    soup = BeautifulSoup(response.content.decode('utf-8'), 'html.parser')
    container = soup.select_one('#officials-card')
    if container is None:
        raise RuntimeError(f'No se encontró la lista de funcionarios en la página {page}.')
    result = []
    for card in container.select('li[aria-labelledby]'):
        name_node = card.select_one('a.institution__higher-official-name')
        charge_node = name_node.find_next_sibling('p') if name_node else None
        if not name_node or not charge_node:
            continue
        name = name_node.get_text(' ', strip=True)
        charge = charge_node.get_text(' ', strip=True).strip(' .')
        href = name_node.get('href', '')
        if not name or not charge or not href.startswith('/institucion/universidad-nacional-micaela-bastidas-de-apurimac/funcionarios/'):
            continue
        official_id = re.search(r'/funcionarios/(\d+)-', href)
        if official_id:
            result.append({'id': 'funcionario:' + official_id.group(1), 'nombre': name,
                           'cargo': charge, 'pagina': page, 'fuente': urljoin(URL, href)})
    if not result:
        raise RuntimeError(f'No se extrajeron funcionarios de la página {page}.')
    return result


def sync():
    """Actualización completa y segura: no borra registros si falla alguna página."""
    rows = [row for page in range(1, 5) for row in fetch_page(page)]
    unique = {row['id']: row for row in rows}
    if len(unique) < 40:
        raise RuntimeError('La lista parece incompleta; se conserva el índice anterior.')
    db = collection()
    existing = db.get(include=['metadatas'])
    old = dict(zip(existing['ids'], existing['metadatas']))
    model = ollama.Client(host=config.OLLAMA_HOST)
    changed = 0
    for row in unique.values():
        document = f"{row['nombre']} — {row['cargo']}"
        meta = {'kind': 'funcionario', 'nombre': row['nombre'], 'cargo': row['cargo'],
                'pagina': row['pagina'], 'fuente': row['fuente'], 'document': document}
        if old.get(row['id']) == meta:
            continue
        vector = model.embed(model=config.EMBEDDING_MODEL, input=document, truncate=False).embeddings[0]
        db.upsert(ids=[row['id']], documents=[document], metadatas=[meta], embeddings=[vector])
        changed += 1
    obsolete = [identity for identity in existing['ids'] if identity.startswith('funcionario:') and identity not in unique]
    if obsolete:
        db.delete(ids=obsolete)
    return {'coleccion': db.name, 'funcionarios': len(unique), 'actualizados': changed,
            'eliminados': len(obsolete), 'paginas': 4, 'fecha_utc': datetime.now(timezone.utc).isoformat()}


def find(query, limit=3):
    """Combina coincidencia por cargo con embedding; devuelve candidatos verificables."""
    db = collection()
    all_rows = db.get(where={'kind': 'funcionario'}, include=['metadatas'])
    if not all_rows['ids']:
        return []
    vector = ollama.Client(host=config.OLLAMA_HOST).embed(
        model=config.EMBEDDING_MODEL, input=query, truncate=False).embeddings[0]
    hits = db.query(query_embeddings=[vector], n_results=min(12, len(all_rows['ids'])),
                    where={'kind': 'funcionario'}, include=['metadatas', 'distances'])
    words = _words(query)
    scored = []
    semantic_hits = {meta['fuente']: distance for meta, distance in zip(hits['metadatas'][0], hits['distances'][0])}
    for meta in all_rows['metadatas']:
        cargo_words = _words(meta['cargo'])
        name_words = _words(meta['nombre'])
        overlap = len(words & (cargo_words | name_words)) / max(1, len(words))
        distance = semantic_hits.get(meta['fuente'])
        semantic = 1 / (1 + max(0, float(distance))) if distance is not None else 0
        gender = 0.15 if ('directora' in _plain(query) and 'directora' in _plain(meta['cargo'])) else 0
        score = 0.7 * overlap + 0.3 * semantic + gender
        if overlap or semantic > 0.65:
            scored.append((score, meta))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [dict(meta, coincidencia=round(score, 3)) for score, meta in scored[:limit]]


if __name__ == '__main__':
    print(json.dumps(sync(), ensure_ascii=False, indent=2))
