"""Modo Normativas / PDF: expone ChatPDF (consultar_pdfs.py) sin modificarlo."""
import copy

import chromadb
import ollama

import config
import consultar_pdfs as cp
from consultar_pdfs_gobpe import BuscadorGobPe

buscador_gobpe = BuscadorGobPe(paginas=3, timeout=10)
referencias_originales = cp.referencias


def referencias_con_enlaces(paginas):
    referencias_originales(paginas)
    buscador_gobpe.mostrar(paginas)


cp.referencias = referencias_con_enlaces

args = cp.argumentos()  # valores por defecto del script
args.db = config.CHROMA_DIR
args.collection = config.COLECCION_DOCS

db = chromadb.PersistentClient(path=str(args.db))
collection = db.get_collection(args.collection, embedding_function=None)
if (collection.metadata or {}).get("embedding_model") != cp.EMBEDDING_MODEL:
    raise RuntimeError("La colección no usa el embedding Qwen3 esperado.")
client = ollama.Client(host=args.host, timeout=600, trust_env=False)

sesiones = {}


def _sesion(sid):
    if sid not in sesiones:
        a = copy.copy(args)
        sesiones[sid] = {"args": a, "chat": cp.ChatPDF(client, collection, a), "source": None}
        if len(sesiones) > 200:  # evita crecer sin límite
            sesiones.pop(next(iter(sesiones)))
    return sesiones[sid]


def _procesar(s, texto):
    chat, a = s["chat"], s["args"]
    c = texto.casefold()
    archivos = chat.catalogo.archivos
    if c == "/pdfs":
        cp.mostrar_pdfs(archivos)
    elif c == "/todos":
        s["source"], chat.pendiente, chat.ultima_pregunta = None, None, ""
        print("Buscaré en todos los documentos.")
    elif c in ("/completo", "/auto"):
        a.modo = c[1:]
        chat.respuestas.clear()
        print(f"Modo cambiado a {a.modo}.")
    elif c == "/recargar":
        s["chat"] = cp.ChatPDF(client, collection, a)
        print("Catálogo actualizado.")
    elif c == "/pdf" or c.startswith("/pdf "):
        try:
            s["source"], consulta = cp.seleccionar_pdf(texto, archivos)
            chat.pendiente, chat.ultima_pregunta = None, ""
        except ValueError as exc:
            print(exc)
            return
        if consulta:
            s["source"] = chat.responder(consulta, s["source"])
        else:
            print("Documento seleccionado. Ya puedes preguntar.")
    elif c.startswith("/"):
        print("Comando desconocido. Usa /pdfs, /pdf N, /todos, /completo o /auto.")
    else:
        s["source"] = chat.responder(texto, s["source"])


def responder(texto, sid):
    """Punto de entrada del modo. Lo que imprime se envía al navegador."""
    if texto:
        _procesar(_sesion(sid), texto)
