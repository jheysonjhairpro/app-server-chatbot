"""Panel local: python -m helper.documentos.server (solo loopback, sin dependencias web)."""

import argparse
import json
import os
import secrets
import threading
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from helper.documentos.jobs import Jobs
from compartido.documentos import DocumentStore, ConflictError, instance_lock
from helper.documentos.worker import index_settings
from motores.portal_unamba.src.config import ADMIN_DIR
from motores.portal_unamba.src.config import APP_ROOT, RAW_DATA_DIR, DATA_DIR, LOGS_DIR, VECTOR_STORE_DIR, OLLAMA_BASE_URL, EMBED_MODEL

ASSETS = Path(__file__).parent / 'web'


def validate_extraction(payload):
    from motores.portal_unamba.extraer_enlaces import permitido
    mode = payload.get('mode', 'urls')
    if mode not in {'urls', 'crawl'}:
        raise ValueError('Selecciona URLs o rastreo de UNAMBA.')
    urls = payload.get('urls', [])
    if not isinstance(urls, list) or any(not isinstance(url, str) for url in urls):
        raise ValueError('La lista de URLs no es vÃ¡lida.')
    urls = list(dict.fromkeys(url.strip() for url in urls if url.strip()))
    if mode == 'urls' and (not urls or len(urls) > 200 or any(not permitido(url) for url in urls)):
        raise ValueError('Introduce entre 1 y 200 pÃ¡ginas de www.unamba.edu.pe o unamba.edu.pe. No se permiten otros dominios, subdominios ni enlaces tÃ©cnicos.')
    maximum = payload.get('max_pages', 50)
    if type(maximum) is not int or not 1 <= maximum <= 1000:
        raise ValueError('El lÃ­mite de rastreo debe estar entre 1 y 1000 pÃ¡ginas.')
    if type(payload.get('force', False)) is not bool:
        raise ValueError('La opciÃ³n de actualizaciÃ³n no es vÃ¡lida.')
    if type(payload.get('cleanup_after', False)) is not bool:
        raise ValueError('La opciÃ³n de limpieza no es vÃ¡lida.')
    if not os.getenv('FIRECRAWL_API_KEY', '').strip():
        raise ValueError('Configura FIRECRAWL_API_KEY en .env y reinicia el panel para extraer pÃ¡ginas.')
    return {'mode': mode, 'urls': urls, 'max_pages': maximum, 'force': payload.get('force', False),
            'cleanup_after': payload.get('cleanup_after', False)}


class AdminServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, store, jobs):
        self.store, self.jobs = store, jobs
        self.token = secrets.token_urlsafe(32)
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, data, status=200, mime='application/json; charset=utf-8'):
        content = json.dumps(data, ensure_ascii=False).encode('utf-8') if mime.startswith('application/json') else data
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(content)

    def allowed(self, mutate=False):
        port = self.server.server_port
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
        if self.headers.get('Host') not in hosts:
            return self.forbidden('Host no permitido.')
        origin = self.headers.get('Origin')
        if origin and origin not in {'http://' + host for host in hosts}:
            return self.forbidden('Origen no permitido.')
        if mutate and not secrets.compare_digest(self.headers.get('X-Admin-Token', ''), self.server.token):
            return self.forbidden('SesiÃ³n no vÃ¡lida. Recarga el panel.')
        return True

    def forbidden(self, message):
        # Consumir cuerpos pequeÃ±os evita que Windows cierre con RST antes
        # de que el cliente reciba la respuesta 403.
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if 0 < size <= 6 * 1024 * 1024:
                self.rfile.read(size)
        except (ValueError, OSError):
            pass
        self.send({'error': message}, 403)
        return False

    def dispatch(self, method):
        self.connection.settimeout(15)
        if not self.allowed(method != 'GET'):
            return
        try:
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            name = query.get('path', [''])[0]
            store, jobs = self.server.store, self.server.jobs
            if method == 'GET':
                if parsed.path in {'/', '/app.js', '/style.css'}:
                    filename, mime = {'/': ('index.html', 'text/html; charset=utf-8'),
                                      '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                                      '/style.css': ('style.css', 'text/css; charset=utf-8')}[parsed.path]
                    return self.send((ASSETS / filename).read_bytes(), mime=mime)
                if parsed.path == '/api/session':
                    return self.send({'token': self.server.token})
                if parsed.path == '/api/documents':
                    return self.send(store.list(query.get('q', [''])[0]))
                if parsed.path == '/api/document':
                    return self.send(store.read(name))
                if parsed.path == '/api/cleanup/diff':
                    from helper.documentos.cleanup import preview_diff
                    with jobs.lock:
                        jobs.require_idle()
                        return self.send(preview_diff(store, query.get('id', [''])[0], name))
                if parsed.path == '/api/archives':
                    return self.send({'items': store.archives(query.get('kind', ['trash'])[0], name or None)})
                if parsed.path == '/api/jobs':
                    return self.send({'jobs': jobs.list(), 'active': jobs.active})
                if parsed.path == '/api/log':
                    return self.send({'text': jobs.log(query.get('id', [''])[0])})
                if parsed.path == '/api/status':
                    state_path = store.state / 'index_state.json'
                    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else None
                    fingerprint = store.fingerprint()
                    return self.send({'document_count': len(store.paths()), 'fingerprint': fingerprint,
                                      'index': state, 'dirty': not state or state['fingerprint'] != fingerprint or state.get('settings') != index_settings() or not (VECTOR_STORE_DIR / 'chroma.sqlite3').exists(),
                                      'index_exists': (VECTOR_STORE_DIR / 'chroma.sqlite3').exists(),
                                      'firecrawl_configured': bool(os.getenv('FIRECRAWL_API_KEY', '').strip()),
                                      'embedding_model': EMBED_MODEL, 'active': jobs.active,
                                      'raw_dir': str(store.root)})
                if parsed.path == '/api/health':
                    try:
                        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                        with opener.open(OLLAMA_BASE_URL + '/api/tags', timeout=2) as response:
                            models = [m.get('name', '') for m in json.load(response).get('models', [])]
                        wanted = EMBED_MODEL if ':' in EMBED_MODEL else EMBED_MODEL + ':latest'
                        return self.send({'online': True, 'embedding_ready': wanted in models, 'models': models})
                    except Exception:
                        return self.send({'online': False, 'embedding_ready': False, 'models': []})
                return self.send({'error': 'Ruta no encontrada.'}, 404)
            size = int(self.headers.get('Content-Length', '0'))
            if size <= 0 or size > 6 * 1024 * 1024:
                return self.send({'error': 'La solicitud debe tener contenido y no superar 6 MB.'}, 413)
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError('La solicitud debe ser un objeto JSON.')
            with jobs.lock:
                if parsed.path == '/api/jobs/cancel' and method == 'POST':
                    jobs.cancel()
                    return self.send({'ok': True})
                jobs.require_idle()
                if parsed.path == '/api/cleanup/preview' and method == 'POST':
                    from helper.documentos.cleanup import preview
                    return self.send(preview(store))
                if parsed.path == '/api/cleanup/apply' and method == 'POST':
                    from helper.documentos.cleanup import read_preview
                    record = read_preview(store, payload.get('preview_id'))
                    return self.send(jobs.start('cleanup', {'preview_id': record['id']}), 202)
                if parsed.path == '/api/documents' and method == 'POST':
                    return self.send(store.save(payload.get('path'), payload.get('content'), create=True), 201)
                if parsed.path == '/api/document' and method == 'PUT':
                    return self.send(store.save(name, payload.get('content'), payload.get('version')))
                if parsed.path == '/api/document' and method == 'DELETE':
                    return self.send(store.delete(name, payload.get('version')))
                if parsed.path == '/api/restore' and method == 'POST':
                    return self.send(store.restore(payload.get('kind'), payload.get('id')))
                if parsed.path == '/api/extract' and method == 'POST':
                    return self.send(jobs.start('extract', validate_extraction(payload)), 202)
                if parsed.path == '/api/index' and method == 'POST':
                    return self.send(jobs.start('index', {'clear_index': payload.get('clear_index') is True}), 202)
                return self.send({'error': 'Ruta no encontrada.'}, 404)
        except ConflictError as exc:
            self.send({'error': str(exc)}, 409)
        except FileNotFoundError:
            self.send({'error': 'El documento o respaldo ya no existe.'}, 404)
        except (ValueError, TypeError, UnicodeError) as exc:
            self.send({'error': str(exc)}, 400)
        except Exception:
            self.send({'error': 'No se pudo completar la operaciÃ³n. Revisa los permisos y el estado del panel.'}, 500)

    def do_GET(self):
        self.dispatch('GET')

    def do_POST(self):
        self.dispatch('POST')

    def do_PUT(self):
        self.dispatch('PUT')

    def do_DELETE(self):
        self.dispatch('DELETE')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=int(os.getenv("UNAMBA_ADMIN_PORT", "8765")))
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    store = DocumentStore(RAW_DATA_DIR, ADMIN_DIR)
    held_lock = instance_lock(store.state / 'panel.lock')
    jobs = Jobs(store, APP_ROOT, LOGS_DIR)
    try:
        server = AdminServer(('127.0.0.1', args.port), store, jobs)
    except OSError as exc:
        raise SystemExit(f'No se pudo abrir el panel. Puede que el puerto {args.port} ya estÃ© en uso. {exc}') from exc
    url = f'http://127.0.0.1:{server.server_port}'
    print(f'Panel de documentos UNAMBA: {url}', flush=True)
    print('Acceso solo desde este equipo. Ctrl+C para cerrar.', flush=True)
    if not args.no_browser:
        threading.Timer(.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nCerrando panel...', flush=True)
    finally:
        server.server_close()
        process = jobs.process
        if process and process.poll() is None:
            if jobs.active and jobs.active['operation'] == 'extract':
                jobs.cancel()
            else:
                print('Esperando que termine la indexaciÃ³n para conservar el Ã­ndice.', flush=True)
            process.wait()
        held_lock.close()


if __name__ == '__main__':
    main()
