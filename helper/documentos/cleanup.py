"""Vista previa versionada de la misma limpieza que utiliza el comando local."""

import difflib
import json
import re
import uuid

from compartido.documentos import ConflictError, atomic_write, now
from motores.portal_unamba.src.ingestion.cleanup import plan, run_cleanup


def preview(store):
    fingerprint = store.fingerprint()
    docs = {p.relative_to(store.root).as_posix(): store.read(p.relative_to(store.root).as_posix()) for p in store.paths()}
    output = plan({name: doc['content'] for name, doc in docs.items()})
    changes = []
    for name, (content, removed) in output.items():
        original = docs[name]['content']
        if content != original:
            changes.append({'path': name, 'removed_menu': removed,
                            'characters_removed': len(original) - len(content)})
    if store.fingerprint() != fingerprint:
        raise ConflictError('Los documentos cambiaron. Genera otra vista previa.')
    record = {'id': uuid.uuid4().hex, 'created_at': now(), 'fingerprint': fingerprint,
              'scanned': len(docs), 'changes': changes}
    atomic_write(store.state / 'cleanup_previews' / (record['id'] + '.json'), json.dumps(record, ensure_ascii=False))
    return record


def read_preview(store, identity):
    if not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{32}', identity):
        raise ValueError('Vista previa no válida.')
    record = json.loads((store.state / 'cleanup_previews' / (identity + '.json')).read_text(encoding='utf-8'))
    if record['fingerprint'] != store.fingerprint():
        raise ConflictError('Los documentos cambiaron. Genera otra vista previa antes de limpiar.')
    return record


def preview_diff(store, identity, name):
    record = read_preview(store, identity)
    if name not in {row['path'] for row in record['changes']}:
        raise ValueError('El documento no pertenece a esta vista previa.')
    docs = {p.relative_to(store.root).as_posix(): store.read(p.relative_to(store.root).as_posix())['content'] for p in store.paths()}
    content = plan(docs)[name][0]
    diff = list(difflib.unified_diff(docs[name].splitlines(), content.splitlines(),
                                   fromfile='Antes', tofile='Después', lineterm=''))
    read_preview(store, identity)
    return {'path': name, 'diff': '\n'.join(diff[:3000]), 'truncated': len(diff) > 3000}


def apply_preview(store, identity):
    read_preview(store, identity)
    return run_cleanup(store, apply=True)
