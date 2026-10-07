"""Modo Portal Web: RAG sobre páginas públicas indexadas de la UNAMBA."""
import config
import web_rag

chat = web_rag.crear_desde_config(config)


def responder(texto, sid):
    """Punto de entrada del modo web. Lo que imprime se envía al navegador."""
    if not texto:
        print("Escribe una pregunta sobre el portal web de la UNAMBA.")
        return
    comando = texto.casefold()
    if comando in ("/estado", "/status"):
        chat.estado()
    elif comando.startswith("/"):
        print("Comando desconocido. Usa /estado o pregunta directamente.")
    else:
        chat.responder(texto)
