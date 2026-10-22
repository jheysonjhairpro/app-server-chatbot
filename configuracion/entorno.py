"""Carga única del .env y resolución de rutas desde la raíz del proyecto."""
import os
import secrets
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / '.env'
load_dotenv(ENV_FILE, override=False, interpolate=False, encoding='utf-8-sig')


def preparar_env():
    """Primera instalación: crea configuración privada desde la plantilla."""
    if not ENV_FILE.exists():
        template = (ROOT / '.env.example').read_text(encoding='utf-8-sig')
        with ENV_FILE.open('x', encoding='utf-8') as handle:
            handle.write(template)
    load_dotenv(ENV_FILE, override=False, interpolate=False, encoding='utf-8-sig')
    if not os.getenv('UNAMBA_TOKEN', '').strip():
        from dotenv import set_key
        token = secrets.token_urlsafe(32)
        set_key(str(ENV_FILE), 'UNAMBA_TOKEN', token)
        os.environ['UNAMBA_TOKEN'] = token


def ruta(name, default):
    value = os.getenv(name, '').strip() or str(default)
    path = Path(value).expanduser()
    return (path if path.is_absolute() else ROOT / path).resolve()


def booleano(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {'1', 'true', 'yes', 'si', 'sí'}


def variables():
    """Valores efectivos para los procesos hijos del lanzador; nunca registrar."""
    return {key: os.environ.get(key, value or '')
            for key, value in dotenv_values(ENV_FILE, interpolate=False, encoding='utf-8-sig').items()}
