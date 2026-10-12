"""Modo Portal Web: RAG sobre páginas públicas indexadas de la UNAMBA."""
import config
import web_rag
from collections import OrderedDict
from threading import Lock
from time import monotonic
from documentos_web import AclaracionWeb, resolver_aclaracion

chat = web_rag.crear_desde_config(config)
sesiones = OrderedDict()
sesiones_lock = Lock()


def historial_web(sid):
    if not sid or sid == "default":
        return []
    with sesiones_lock:
        item = sesiones.get(sid)
        return list(item[1]) if item and monotonic() - item[0] < 3600 else []


def pendiente_web(sid):
    with sesiones_lock:
        item = sesiones.get(sid)
        return item[2] if item and len(item) > 2 and monotonic() - item[0] < 3600 else None


def recordar(sid, consulta, pendiente=None):
    if not sid or sid == "default":
        return
    with sesiones_lock:
        previo = sesiones.pop(sid, (0, []))
        historial = previo[1] if monotonic() - previo[0] < 3600 else []
        sesiones[sid] = (monotonic(), (historial + [consulta])[-6:], pendiente)
        while len(sesiones) > 200:
            sesiones.popitem(last=False)


def responder(texto, sid):
    """Punto de entrada del modo web. Lo que imprime se envía al navegador."""
    if not texto:
        print("Escribe una pregunta sobre el portal web de la UNAMBA.")
        return
    comando = texto.casefold()
    if comando in ("/estado", "/status"):
        chat.estado()
    elif comando in ("/fuentes", "/sources"):
        chat.describir_fuentes()
    elif comando.startswith("/"):
        print("Comando desconocido. Usa /estado, /fuentes o pregunta directamente.")
    else:
        pendiente = pendiente_web(sid)
        if pendiente:
            candidatos = resolver_aclaracion(texto, pendiente, web_rag.normalizar)
            if len(candidatos) == 1:
                meta = candidatos[0]
                consulta = pendiente.pregunta + '. Periodo seleccionado: ' + meta['title']
                resultado = chat.responder(pendiente.pregunta, consulta=consulta, urls=[meta['url']], session_id=sid)
                recordar(sid, consulta, resultado)
                return
            if len(candidatos) > 1:
                print('¿Qué ciclo necesitas? ' + '; '.join(dict.fromkeys(m['title'] for m in candidatos)))
                recordar(sid, pendiente.pregunta, AclaracionWeb(pendiente.pregunta, tuple(candidatos)))
                return
            dominios_nuevos = set(chat._dominios_sugeridos(texto))
            dominios_pendientes = {m['domain'] for m in pendiente.candidatos if m.get('domain')}
            if not dominios_nuevos or not dominios_nuevos.isdisjoint(dominios_pendientes):
                print('No pude identificar el ciclo. Indica el año o el nombre del ciclo que necesitas.')
                recordar(sid, pendiente.pregunta, pendiente)
                return
        consulta = chat.resolver_seguimiento(texto, historial_web(sid))
        resultado = chat.responder(texto, consulta=consulta, session_id=sid)
        recordar(sid, consulta, resultado)
