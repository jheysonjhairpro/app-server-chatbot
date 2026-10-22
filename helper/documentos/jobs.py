"""Procesos en segundo plano: un escritor del corpus o del Ã­ndice a la vez."""

import json
import os
import subprocess
import sys
import threading
import uuid
import time
from pathlib import Path

from compartido.documentos import atomic_write, now, ConflictError


def process_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = wintypes.DWORD()
        try:
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class Jobs:
    def __init__(self, store, project, logs):
        self.store, self.project = store, Path(project)
        self.logs = Path(logs) / 'admin'
        self.logs.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.active = None
        self.process = None
        recovered = []
        for path in (store.state / 'jobs').glob('*.json'):
            record = json.loads(path.read_text(encoding='utf-8'))
            if record['status'] in {'running', 'stopping'}:
                if process_alive(record.get('pid')):
                    recovered.append(record)
                    continue
                record.update(status='interrupted', finished=now())
                atomic_write(path, json.dumps(record, ensure_ascii=False))
        if recovered:
            self.active = recovered[-1]
            threading.Thread(target=self._recover, args=(recovered,), daemon=True).start()

    def _recover(self, records):
        while records:
            time.sleep(1)
            with self.lock:
                for record in records[:]:
                    if not process_alive(record.get('pid')):
                        record.update(status='interrupted', finished=now())
                        self._save(record)
                        records.remove(record)
                self.active = records[-1] if records else None

    def require_idle(self):
        if self.active:
            raise ConflictError('Hay un proceso en curso. Espera a que termine para cambiar documentos o iniciar otro.')

    def _save(self, record):
        atomic_write(self.store.state / 'jobs' / (record['id'] + '.json'), json.dumps(record, ensure_ascii=False))

    def list(self):
        rows = [json.loads(p.read_text(encoding='utf-8')) for p in (self.store.state / 'jobs').glob('*.json')]
        return sorted(rows, key=lambda r: r['started'], reverse=True)[:30]

    def log(self, identity):
        if not isinstance(identity, str) or len(identity) != 32 or any(c not in '0123456789abcdef' for c in identity):
            raise ValueError('Proceso no vÃ¡lido.')
        path = self.logs / (identity + '.log')
        if not path.exists():
            raise FileNotFoundError('No existe el registro del proceso.')
        with path.open('rb') as handle:
            handle.seek(max(0, path.stat().st_size - 80000))
            return handle.read().decode('utf-8', errors='replace')

    def start(self, operation, options=None):
        with self.lock:
            self.require_idle()
            if operation not in {'extract', 'index', 'cleanup'}:
                raise ValueError('OperaciÃ³n no vÃ¡lida.')
            options = options or {}
            if operation == 'index' and not self.store.paths() and options.get('clear_index') is not True:
                raise ValueError('La biblioteca estÃ¡ vacÃ­a. Confirma explÃ­citamente el vaciado del Ã­ndice.')
            identity = uuid.uuid4().hex
            record = {'id': identity, 'operation': operation, 'options': options,
                      'status': 'running', 'started': now(), 'finished': None, 'exit_code': None}
            self._save(record)
            self.active = record
            env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUNBUFFERED='1',
                       RAG_RAW_DIR=str(self.store.root), RAG_ADMIN_DIR=str(self.store.state),
                       PYTHONPATH=os.pathsep.join([str(self.project), os.environ.get('PYTHONPATH', '')]))
            command = [sys.executable, '-u', '-m', 'helper.documentos.worker', operation,
                       str(self.store.state / 'jobs' / (identity + '.json'))]
            try:
                log = (self.logs / (identity + '.log')).open('wb')
                try:
                    self.process = subprocess.Popen(command, cwd=self.project, env=env, stdout=log,
                                                    stderr=subprocess.STDOUT,
                                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                    record['pid'] = self.process.pid
                    self._save(record)
                finally:
                    log.close()
            except Exception:
                record.update(status='failed', finished=now(), exit_code=-1)
                self._save(record)
                self.active = None
                raise
            threading.Thread(target=self._wait, args=(self.process, record), daemon=True).start()
            return record

    def _wait(self, process, record):
        code = process.wait()
        with self.lock:
            stopped = record['status'] == 'stopping'
            record.update(status='cancelled' if stopped else 'succeeded' if code == 0 else 'failed',
                          finished=now(), exit_code=code)
            self._save(record)
            self.active = self.process = None

    def cancel(self):
        with self.lock:
            if not self.active or not self.process:
                raise ValueError('No hay una extracciÃ³n en curso.')
            if self.active['operation'] != 'extract':
                raise ValueError('La indexaciÃ³n debe terminar para publicar el Ã­ndice de forma segura.')
            self.active['status'] = 'stopping'
            self._save(self.active)
            if os.name == 'nt':
                # El ejecutable de un venv puede envolver otro proceso Python.
                # Se detiene Ãºnicamente el Ã¡rbol del trabajador de este panel.
                result = subprocess.run(['taskkill', '/PID', str(self.process.pid), '/T', '/F'],
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        creationflags=subprocess.CREATE_NO_WINDOW, check=False)
                if result.returncode and self.process.poll() is None:
                    self.active['status'] = 'running'
                    self._save(self.active)
                    raise ValueError('No se pudo detener la extracciÃ³n. Revisa el registro del proceso.')
            else:
                self.process.terminate()
