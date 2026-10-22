"""Punto de entrada aislado para trabajos de la interfaz local."""

import json
import os
import sys
from pathlib import Path

from compartido.documentos import DocumentStore, atomic_write, now
from motores.portal_unamba.src.config import ADMIN_DIR
from motores.portal_unamba.src.config import APP_ROOT, RAW_DATA_DIR, DATA_DIR, EMBED_MODEL, CHUNK_SIZE, CHUNK_OVERLAP, CHROMA_COLLECTION, VECTOR_STORE_DIR


def index_settings():
    return {'embedding_model': EMBED_MODEL, 'chunk_size': CHUNK_SIZE,
            'chunk_overlap': CHUNK_OVERLAP, 'collection': CHROMA_COLLECTION,
            'persist_dir': (VECTOR_STORE_DIR.resolve().relative_to(APP_ROOT).as_posix() if VECTOR_STORE_DIR.resolve().is_relative_to(APP_ROOT) else str(VECTOR_STORE_DIR.resolve())), 'schema_version': 3}


def main():
    operation, job_path = sys.argv[1:]
    options = json.loads(Path(job_path).read_text(encoding='utf-8'))['options']
    store = DocumentStore(RAW_DATA_DIR, ADMIN_DIR)
    if operation == 'cleanup':
        from helper.documentos.cleanup import apply_preview
        result = apply_preview(store, options.get('preview_id'))
        print(json.dumps(result, ensure_ascii=False), flush=True)
        print('Limpieza terminada con respaldos. Actualiza el índice para aplicar los cambios al chatbot.', flush=True)
    elif operation == 'index':
        from motores.portal_unamba.src.ingestion.indexer import build_index, build_empty_index
        fingerprint = store.fingerprint()
        print('Construyendo índice con Markdown Header Chunking...', flush=True)
        if store.paths():
            count = build_index()
        elif options.get('clear_index') is True:
            print('Biblioteca vacía: retirando del índice las fuentes anteriores.', flush=True)
            count = build_empty_index()
        else:
            raise ValueError('La biblioteca quedó vacía. Confirma el vaciado desde el panel.')
        unchanged = fingerprint == store.fingerprint()
        state = {'fingerprint': fingerprint, 'indexed_at': now(), 'chunks': count, 'settings': index_settings()}
        atomic_write(store.state / 'index_state.json', json.dumps(state))
        print(f'Índice actualizado: {count} fragmentos.', flush=True)
        print('En el chatbot ejecuta /recargar o reinícialo.', flush=True)
        if not unchanged:
            print('Advertencia: los documentos cambiaron externamente durante la indexación. Vuelve a indexar.', flush=True)
    elif operation == 'extract':
        from motores.portal_unamba.extraer_firecrawl import extraer_urls, urls_descubiertas
        from motores.portal_unamba.extraer_enlaces import main as discover
        urls = options.get('urls', [])
        if options.get('mode') == 'crawl':
            output = DATA_DIR / 'crawl'
            print('Descubriendo páginas de UNAMBA...', flush=True)
            discover(['--salida', str(output), '--max-paginas', str(options['max_pages']), '--timeout', os.getenv('PORTAL_CRAWL_TIMEOUT', '25')])
            urls = urls_descubiertas(output / 'enlaces_validos.json')
        else:
            requested = urls
            output = DATA_DIR / 'crawl'
            arguments = ['--salida', str(output), '--max-paginas', str(options.get('max_pages', int(os.getenv('PORTAL_CRAWL_MAX_PAGES', '200')))), '--timeout', os.getenv('PORTAL_CRAWL_TIMEOUT', '25')]
            for url in requested:
                arguments.extend(['--objetivo', url])
            print('Verificando que las URLs sean accesibles desde la interfaz de UNAMBA...', flush=True)
            discover(arguments)
            accessible = set(urls_descubiertas(output / 'enlaces_validos.json'))
            from motores.portal_unamba.src.ingestion.url_policy import page_url
            urls = list(dict.fromkeys(page_url(url) for url in requested))
            if not set(urls) <= accessible:
                raise ValueError('Hay URLs no accesibles desde los enlaces visibles de UNAMBA o pendientes por el límite del rastreo. No se inició la extracción.')
        print(f'Extrayendo {len(urls)} páginas. Firecrawl consume créditos.', flush=True)
        extraer_urls(urls, force=options.get('force', False), before_write=store.backup_external)
        if options.get('cleanup_after', False):
            from motores.portal_unamba.src.ingestion.cleanup import run_cleanup
            print('Aplicando limpieza conservadora con respaldos automáticos...', flush=True)
            print(json.dumps(run_cleanup(store, apply=True), ensure_ascii=False), flush=True)
        print('Documentos disponibles. Revisa el contenido y reconstruye el índice.', flush=True)
    else:
        raise ValueError('Operación desconocida.')


if __name__ == '__main__':
    main()
