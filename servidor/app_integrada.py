"""Entrada compatible del servidor integrado; las rutas se registran en app.py."""
import os

from app import app
import config


if __name__ == '__main__':
    app.run(host=os.environ.get('UNAMBA_HOST', config.HOST),
            port=int(os.environ.get('UNAMBA_PORT', config.PORT)), threaded=True)
