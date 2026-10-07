"""Arranque del servidor de IA. Ejecutar desde C:\\IA_UNAMBA:  python servidor\\app.py"""
from flask import Flask, abort, jsonify, request

import config
from comun import transmitir
from modos import docs, web

app = Flask(__name__)


@app.before_request
def verificar_token():
    if request.path == "/salud":
        return
    if request.headers.get("X-Token") != config.TOKEN:
        abort(403)


@app.get("/salud")
def salud():
    return jsonify(ok=True)


def _entrada():
    d = request.get_json(force=True, silent=True) or {}
    texto = str(d.get("message") or "").strip()[:1000]
    sid = str(d.get("session") or "default")[:64]
    return texto, sid


@app.post("/chat/docs")
def chat_docs():
    texto, sid = _entrada()
    return transmitir(lambda: docs.responder(texto, sid))


@app.post("/chat/web")
def chat_web():
    texto, sid = _entrada()
    return transmitir(lambda: web.responder(texto, sid))


if __name__ == "__main__":
    app.run(host=config.HOST, port=config.PORT, threaded=True)