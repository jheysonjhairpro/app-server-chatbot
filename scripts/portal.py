"""Lanzador de módulos del portal y del panel desde cualquier directorio."""
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = {
    'chatbot': 'motores.portal_unamba.main',
    'panel': 'helper.documentos.server',
    'indexar': 'motores.portal_unamba.src.ingestion.indexer',
    'extraer': 'motores.portal_unamba.extraer_firecrawl',
    'limpiar': 'motores.portal_unamba.src.ingestion.cleanup',
    'enlaces': 'motores.portal_unamba.extraer_enlaces',
}

if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        raise SystemExit('Uso: portal.py chatbot|panel|indexar|extraer|limpiar|enlaces [opciones]')
    target = COMMANDS[sys.argv[1]]
    sys.argv = [target] + sys.argv[2:]
    sys.path.insert(0, str(ROOT))
    runpy.run_module(target, run_name='__main__')
