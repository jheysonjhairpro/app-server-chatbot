"""API de la UNAMBA: python servidor/app.py o iniciar.bat."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flask import Flask, abort, jsonify, request, Response

from configuracion import servidor as config
from servidor.comun import transmitir
from servidor.modos import docs, web

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


@app.post('/fut/chat')
def fut_chat():
    from motores.fut_unamba.agent import respond
    texto, sid = _entrada()
    try:
        return jsonify(respond(sid, texto))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except Exception:
        app.logger.exception('No se pudo completar el diálogo FUT')
        return jsonify(error='No se pudo consultar el asistente FUT. Revisa Ollama y la colección Fut_UnambaDB.'), 503


@app.get('/fut/download/<token>')
def fut_download(token):
    from motores.fut_unamba.agent import download
    if len(token) > 80:
        abort(404)
    data = download(token)
    if data is None:
        abort(404)
    return Response(data, mimetype='application/pdf', headers={
        'Content-Disposition': 'attachment; filename="FUT-UNAMBA.pdf"',
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
    })


if __name__ == "__main__":
    app.run(host=config.HOST, port=config.PORT, threaded=True)
