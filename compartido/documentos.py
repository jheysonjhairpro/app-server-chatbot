"""Documentos con control de versiones, respaldos y papelera fuera del corpus."""

import hashlib
import json
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit


class ConflictError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.admin-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class DocumentStore:
    def __init__(self, root, state):
        self.root = Path(root).resolve()
        self.state = Path(state).resolve()
        if self.state.is_relative_to(self.root):
            raise ValueError('Los respaldos y la papelera deben guardarse fuera de la carpeta de documentos.')
        self.root.mkdir(parents=True, exist_ok=True)
        self.state.mkdir(parents=True, exist_ok=True)

    def path(self, name):
        if not isinstance(name, str) or not name or '\\' in name or ':' in name:
            raise ValueError('Nombre de documento no válido.')
        parts = Path(name).parts
        if any(re.search(r'[<>"|?*\x00-\x1f]', part) for part in parts):
            raise ValueError('El nombre contiene caracteres no permitidos.')
        if any(p in {'.', '..'} or p.startswith('.') or p.endswith((' ', '.')) for p in parts):
            raise ValueError('La ruta del documento no está permitida.')
        if any(re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', p, re.I) for p in parts):
            raise ValueError('Ese nombre está reservado por Windows.')
        target = (self.root / name).resolve()
        if not target.is_relative_to(self.root) or target == self.root or target.suffix.lower() not in {'.md', '.txt'}:
            raise ValueError('Solo se admiten documentos .md y .txt dentro de la carpeta de documentos del portal.')
        return target

    def paths(self):
        return sorted((p for p in self.root.rglob('*') if p.is_file()
                       and p.suffix.lower() in {'.md', '.txt'}
                       and p.resolve().is_relative_to(self.root)
                       and not any(part.startswith('.') for part in p.relative_to(self.root).parts)), key=str)

    def read(self, name):
        path = self.path(name)
        data = path.read_bytes()
        content = data.decode('utf-8-sig')
        url = re.search(r'^\s*(?:url|source|fuente):\s*(https?://\S+)', content, re.M | re.I)
        title = re.search(r'^title:\s*(.+)$', content, re.M)
        heading = re.search(r'^#{1,6}\s+(.+)$', content, re.M)
        if title:
            label = title[1]
        elif url:
            parsed = urlsplit(url[1])
            slug = unquote(parsed.path).strip('/').split('/')[-1].replace('-', ' ').replace('_', ' ')
            label = (slug[:1].upper() + slug[1:]) if slug else f'Portada · {parsed.hostname}'
            if parsed.query:
                label += ' · ' + parsed.query
        else:
            label = heading[1] if heading else path.stem
        return {'path': path.relative_to(self.root).as_posix(), 'title': label,
                'url': url[1] if url else '', 'content': content, 'size': len(data),
                'modified': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                'version': hashlib.sha256(data).hexdigest()}

    def list(self, query=''):
        rows = []
        errors = []
        for path in self.paths():
            name = path.relative_to(self.root).as_posix()
            try:
                row = self.read(name)
            except (OSError, UnicodeError) as exc:
                errors.append({'path': name, 'error': str(exc)})
                continue
            if query.casefold() not in (name + row['content']).casefold():
                continue
            row.pop('content')
            rows.append(row)
        return {'documents': rows, 'errors': errors}

    def fingerprint(self):
        digest = hashlib.sha256()
        for path in self.paths():
            digest.update(path.relative_to(self.root).as_posix().encode())
            digest.update(b'\0')
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        return digest.hexdigest()

    def _archive(self, name, kind):
        document = self.read(name)
        identity = uuid.uuid4().hex
        folder = self.state / kind / identity
        folder.mkdir(parents=True)
        atomic_write(folder / 'content.txt', document.pop('content'))
        document.update(id=identity, archived=now())
        atomic_write(folder / 'record.json', json.dumps(document, ensure_ascii=False))
        return document

    def backup_external(self, path):
        path = Path(path).resolve()
        if path.exists():
            self._archive(path.relative_to(self.root).as_posix(), 'backups')

    def save(self, name, content, version=None, create=False):
        path = self.path(name)
        if not isinstance(content, str) or not content.strip():
            raise ValueError('El contenido del documento no puede estar vacío.')
        if len(content.encode('utf-8')) > 4 * 1024 * 1024:
            raise ValueError('El documento supera el límite de 4 MB.')
        if create:
            if path.exists():
                raise ConflictError('Ya existe un documento con ese nombre. No se sobrescribió.')
        else:
            if not path.exists():
                raise FileNotFoundError('El documento ya no existe.')
            if self.read(name)['version'] != version:
                raise ConflictError('El documento cambió desde que lo abriste. Recárgalo antes de guardar.')
            self._archive(name, 'backups')
        atomic_write(path, content)
        return self.read(name)

    def delete(self, name, version):
        if self.read(name)['version'] != version:
            raise ConflictError('El documento cambió. Recárgalo antes de eliminarlo.')
        archived = self._archive(name, 'trash')
        self.path(name).unlink()
        return archived

    def archives(self, kind, name=None):
        if kind not in {'trash', 'backups'}:
            raise ValueError('Archivo no válido.')
        rows = []
        for path in (self.state / kind).glob('*/record.json'):
            row = json.loads(path.read_text(encoding='utf-8'))
            if name is None or row['path'] == name:
                rows.append(row)
        return sorted(rows, key=lambda r: r['archived'], reverse=True)

    def restore(self, kind, identity):
        if kind not in {'trash', 'backups'} or not re.fullmatch(r'[a-f0-9]{32}', identity or ''):
            raise ValueError('Respaldo no válido.')
        folder = self.state / kind / identity
        record = json.loads((folder / 'record.json').read_text(encoding='utf-8'))
        content = (folder / 'content.txt').read_text(encoding='utf-8')
        target = self.path(record['path'])
        if kind == 'trash' and target.exists():
            raise ConflictError('Ya existe un documento con ese nombre. No se sobrescribió.')
        result = self.save(record['path'], content, version=self.read(record['path'])['version'] if target.exists() else None,
                           create=not target.exists())
        if kind == 'trash':
            backup = self.state / 'backups' / identity
            backup.parent.mkdir(parents=True, exist_ok=True)
            folder.rename(backup)
        return result


def instance_lock(path):
    handle = path.open('a+b')
    try:
        if path.stat().st_size == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return handle
    except OSError:
        handle.close()
        raise SystemExit('Ya hay un panel abierto para esta biblioteca. Usa la ventana existente.')
