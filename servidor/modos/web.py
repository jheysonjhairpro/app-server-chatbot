"""Adaptador HTTP del motor web original, ejecutado en un proceso independiente."""
import json
import os

import httpx
from flask import Blueprint, Response, jsonify, request, stream_with_context

router = Blueprint('chatbot_web', __name__)
BACKEND = 'http://127.0.0.1:' + os.environ.get('UNAMBA_WEB_PORT', '8002')


@router.get('/salud/web')
def salud_web():
    try:
        with httpx.Client(timeout=120, trust_env=False) as client:
            response = client.get(BACKEND + '/health')
        return Response(response.content, status=response.status_code, content_type='application/json')
    except httpx.HTTPError:
        return jsonify(ok=False, error='El motor web no está disponible.'), 503


@router.post('/chat/web')
def chat_web():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get('message'), str):
        return jsonify(error='Envía un objeto JSON con message de tipo texto.'), 400
    question = body['message'].strip()
    if not 2 <= len(question) <= 2000:
        return jsonify(error='La pregunta debe tener entre 2 y 2000 caracteres.'), 400
    client = httpx.Client(timeout=httpx.Timeout(180, connect=5), trust_env=False)
    try:
        upstream = client.send(client.build_request('POST', BACKEND + '/rag/preguntar',
            json={'pregunta': question, 'streaming': True}), stream=True)
        upstream.raise_for_status()
    except httpx.HTTPError:
        if 'upstream' in locals():
            upstream.close()
        client.close()
        return jsonify(error='No se pudo consultar el motor web. Revisa Ollama y el índice.'), 503

    @stream_with_context
    def generate():
        finished = False
        try:
            for line in upstream.iter_lines():
                if not line.startswith('data: '):
                    continue
                event = json.loads(line[6:])
                if event.get('tipo') == 'token':
                    yield event.get('texto', '')
                elif event.get('tipo') == 'error':
                    yield '\nError en el motor web: ' + event.get('detalle', 'Error desconocido')
                    finished = True
                    break
                elif event.get('tipo') == 'fin':
                    sources = event.get('fuentes', [])
                    if sources:
                        yield '\n\nFuentes consultadas:\n'
                        for source in sources:
                            yield f"- {source['titulo']}: {source['url']}\n"
                    if event.get('metricas', {}).get('motivo_fin') == 'length':
                        yield '\nLa respuesta alcanzó el límite de tokens; puede estar incompleta.\n'
                    finished = True
                    break
            if not finished:
                yield '\nLa conexión terminó antes de completar la respuesta.'
        except (httpx.HTTPError, ValueError, KeyError):
            yield '\nSe interrumpió la respuesta del motor web.'
        finally:
            upstream.close()
            client.close()

    return Response(generate(), content_type='text/plain; charset=utf-8',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
