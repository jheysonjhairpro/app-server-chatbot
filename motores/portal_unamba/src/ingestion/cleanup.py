"""Limpieza conservadora; ejecutar primero sin --apply para revisar el informe."""

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
import uuid

from compartido.documentos import DocumentStore, atomic_write, now
from motores.portal_unamba.src.config import ADMIN_DIR
from motores.portal_unamba.src.config import DATA_DIR, RAW_DATA_DIR


def compact_blank_lines(text):
    """Conserva líneas no vacías, front matter y bloques de código literalmente."""
    result = []
    lines = text.splitlines(keepends=True)
    indented_blanks = set()
    # Las líneas vacías internas también forman parte del código indentado.
    start = None
    previous_indented = False
    for index, line in enumerate(lines):
        if not line.strip():
            if start is None:
                start = index
            continue
        indented = line.startswith(('    ', '\t'))
        if start is not None and previous_indented and indented:
            indented_blanks.update(range(start, index))
        start = None
        previous_indented = indented
    fence = None
    front = text.splitlines()[0:1] == ['---']
    blanks = 0
    for index, line in enumerate(lines):
        stripped = line.rstrip('\r\n')
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', stripped)
        protected = front or fence is not None or bool(marker) or line.startswith(('    ', '\t')) or index in indented_blanks
        if protected or stripped.strip() or blanks == 0:
            result.append(line)
        blanks = blanks + 1 if not stripped.strip() else 0
        if front:
            if index and stripped in {'---', '...'}:
                front = False
        elif marker:
            token, tail = marker.groups()
            if fence is None:
                fence = (token[0], len(token))
            elif token[0] == fence[0] and len(token) >= fence[1] and not tail.strip():
                fence = None
    return ''.join(result)


def navigation(text):
    """Reconoce únicamente el menú inicial de UNAMBA, antes del contenido."""
    source = re.search(r'^url:\s*(https?://\S+)', text, re.M)
    if not source or urlsplit(source[1]).hostname != 'www.unamba.edu.pe':
        return None
    match = re.search(r'^- \[Inicio\]\(https://www\.unamba\.edu\.pe/\)\r?$', text, re.M)
    if not match:
        return None
    prefix = text[:match.start()]
    # Nunca quitar listas que aparezcan dentro de una sección del documento.
    if re.search(r'^#{1,6}\s+(?!Contenido extraído\s*$).+', prefix, re.M):
        return None
    lines = text[match.start():].splitlines(keepends=True)
    block = []
    labels = []
    for line in lines:
        link = re.fullmatch(r'( *)- \[([^\]\n]+)\]\((https?://.*)\)\s*', line)
        if not link:
            break
        block.append(line)
        # Solo varían anclas de la página actual, no los destinos informativos.
        destination = re.sub(r'https://www\.unamba\.edu\.pe/[^\s)]*#', 'https://www.unamba.edu.pe/#', link[3])
        labels.append((link[1], link[2], destination))
    if len(block) < 40 or not labels or labels[-1][1] != 'Servicios Universitarios':
        return None
    return match.start(), match.start() + sum(map(len, block)), tuple(labels)


def plan(documents):
    menus = {name: navigation(text) for name, text in documents.items()}
    counts = Counter(menu[2] for menu in menus.values() if menu)
    retained = set()
    output = {}
    for name, text in sorted(documents.items()):
        menu = menus[name]
        removed = False
        if menu and counts[menu[2]] >= 3:
            if menu[2] in retained:
                text = text[:menu[0]] + text[menu[1]:]
                removed = True
            retained.add(menu[2])
        output[name] = (compact_blank_lines(text), removed)
    return output


def run_cleanup(store, apply=False):
    docs = {p.relative_to(store.root).as_posix(): store.read(p.relative_to(store.root).as_posix()) for p in store.paths()}
    changes = plan({name: doc['content'] for name, doc in docs.items()})
    batch = store.state / 'cleanup' / uuid.uuid4().hex
    batch.mkdir(parents=True)
    report = {'created_at': now(), 'applied': apply, 'scanned': len(docs), 'changes': []}
    for name, (content, removed) in changes.items():
        original = docs[name]
        if content == original['content']:
            continue
        # Fuera del menú reconocido solo se permite retirar líneas vacías.
        expected = original['content']
        if removed:
            start, end, _ = navigation(expected)
            expected = expected[:start] + expected[end:]
        assert [x for x in expected.splitlines() if x.strip()] == [x for x in content.splitlines() if x.strip()]
        assert compact_blank_lines(content) == content
        row = {'path': name, 'before_sha256': original['version'],
               'after_sha256': hashlib.sha256(content.encode('utf-8')).hexdigest(),
               'removed_menu': removed, 'characters_removed': len(original['content']) - len(content)}
        if apply:
            raw = store.path(name).read_bytes()
            if hashlib.sha256(raw).hexdigest() != original['version']:
                raise RuntimeError(f'El documento cambió: {name}')
            backup = batch / 'originals' / name
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(raw)
            store.save(name, content, version=original['version'])
        report['changes'].append(row)
        atomic_write(batch / 'report.json', json.dumps(report, ensure_ascii=False, indent=2))
    atomic_write(batch / 'report.json', json.dumps(report, ensure_ascii=False, indent=2))
    return {'scanned': len(docs), 'changed': len(report['changes']),
            'menus_removed': sum(row['removed_menu'] for row in report['changes']),
            'characters_removed': sum(row['characters_removed'] for row in report['changes']),
            'applied': apply, 'report': str(batch / 'report.json')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    store = DocumentStore(RAW_DATA_DIR, ADMIN_DIR)
    from compartido.documentos import instance_lock
    with instance_lock(store.state / 'panel.lock'):
        print(json.dumps(run_cleanup(store, args.apply), ensure_ascii=False))


if __name__ == '__main__':
    main()
