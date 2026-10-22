"""Adaptador del chatbot del portal al transporte de streaming del servidor."""
from threading import RLock
from collections import OrderedDict
from time import monotonic

_engine = None
_lock = RLock()
_sessions = OrderedDict()


def motor():
    global _engine
    if _engine is None:
        from motores.portal_unamba.src.retrieval.engine import RAGEngine
        _engine = RAGEngine()
    return _engine


def responder(texto, sid):
    """Reutiliza el índice y aísla el historial de cada sesión del servidor."""
    global _engine
    if not texto:
        print('Escribe una pregunta sobre la UNAMBA.', flush=True)
        return
    with _lock:
        now = monotonic()
        for key, (updated, _) in list(_sessions.items()):
            if now - updated >= 3600:
                del _sessions[key]
        command = texto.strip().casefold()
        if command == '/limpiar':
            _sessions.pop(sid, None)
            print('Conversación reiniciada.', flush=True)
            return
        if command == '/recargar':
            from motores.portal_unamba.src.retrieval.engine import RAGEngine
            refreshed = RAGEngine()
            _engine = refreshed
            print('Índice recargado.', flush=True)
            return
        chat = motor()
        if command in ('/estado', '/status'):
            print('Portal UNAMBA: índice cargado. Fuentes: www.unamba.edu.pe y unamba.edu.pe.', flush=True)
            return
        if command in ('/fuentes', '/sources'):
            print('Fuentes: documentos institucionales en datos/portal/documentos.', flush=True)
            return
        if command.startswith('/'):
            print('Comandos: /estado, /fuentes, /limpiar, /recargar.', flush=True)
            return
        chat.reset_history()
        if sid and sid != 'default':
            chat.history = list(_sessions.get(sid, (now, []))[1])
        try:
            answer = chat.ask(texto, on_token=lambda chunk: print(chunk, end='', flush=True))
            print('', flush=True)
            if answer.sources:
                print('\nFuentes consultadas:', flush=True)
                for i, source in enumerate(answer.sources, 1):
                    print(f'[{source.number or i}] {source.title}\n{source.url}', flush=True)
            if sid and sid != 'default':
                _sessions[sid] = (now, list(chat.history))
                _sessions.move_to_end(sid)
                while len(_sessions) > 200:
                    _sessions.popitem(last=False)
        finally:
            chat.reset_history()
