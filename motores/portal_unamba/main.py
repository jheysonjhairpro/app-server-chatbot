"""CLI interactiva del chatbot RAG."""

import argparse
import sys
import webbrowser

from motores.portal_unamba.src.bot.sources import print_sources, web_url

HELP = "Comandos: /salir, /limpiar (olvidar conversación), /estado, /recargar (leer índice actualizado), /fuente N (abrir página original), /ayuda."


def main() -> None:
    # Windows PowerShell puede usar cp1252 aunque los fragmentos contengan Unicode.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Consulta documentos de la UNAMBA con un RAG local.")
    parser.add_argument("--show-sources", action="store_true", help="Muestra también los fragmentos recuperados; las URLs siempre se muestran")
    args = parser.parse_args()

    from motores.portal_unamba.src.retrieval.engine import RAGEngine

    print("Iniciando chatbot local (cargando índice y modelos)...")
    try:
        engine = RAGEngine(allow_empty=True)
    except Exception as exc:
        raise SystemExit(f"No se pudo iniciar: {exc}") from exc
    if not engine.has_documents:
        print("Modo sin documentos: puedes conversar; las consultas requieren cargar e indexar archivos.")
    print(HELP)

    last_sources = []
    while True:
        try:
            question = input("\nTú: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nHasta luego.")
            break
        command = question.lower()
        if command in {"/salir", "salir", "/exit", "exit", "quit"}:
            print("Hasta luego.")
            break
        if command == "/fuente" or command.startswith("/fuente "):
            try:
                wanted = int(command.split()[1])
                source = next(source for index, source in enumerate(last_sources, 1)
                              if (source.number or index) == wanted)
                url = web_url(source.url)
                if not url:
                    print("Esta fuente es un archivo local sin URL de origen.")
                elif not webbrowser.open(url):
                    print(f"Abre este enlace en tu navegador: {url}")
            except (ValueError, IndexError, StopIteration):
                print("Usa /fuente N con el número de una fuente de la última respuesta.")
            except webbrowser.Error as exc:
                print(f"No se pudo abrir el navegador: {exc}")
            continue
        if command in {"/limpiar", "/reset"}:
            engine.reset_history()
            last_sources = []
            print("Conversación reiniciada.")
            continue
        if command in {"/ayuda", "/help"}:
            print(HELP)
            continue
        if command == "/estado":
            print(f"Fragmentos indexados: {engine.document_count}. "
                  f"Conversaciones recordadas: {len(engine.history)}.")
            continue
        if command == "/recargar":
            try:
                updated = RAGEngine(allow_empty=True)
            except Exception as exc:
                print(f"No se pudo recargar: {exc}")
            else:
                engine = updated
                last_sources = []
                print(f"Índice recargado: {engine.document_count} fragmentos. Conversación reiniciada.")
            continue
        if not question:
            continue
        answer_started = False

        def emit_token(token):
            nonlocal answer_started
            if not answer_started:
                print('Bot: ', end='', flush=True)
                answer_started = True
            print(token, end='', flush=True)

        try:
            print()
            result = engine.ask(question, on_token=emit_token,
                                on_status=lambda message: print(message, flush=True))
            print()
        except KeyboardInterrupt:
            print("\nRespuesta interrumpida.")
            continue
        except Exception as exc:
            print(f"\nError al consultar ({type(exc).__name__}): {exc}")
            continue
        last_sources = result.sources
        if args.show_sources and result.standalone_question and result.standalone_question != question:
            print(f"\n(Consulta interpretada como: {result.standalone_question})")
        print_sources(result.sources, show_excerpts=args.show_sources)



if __name__ == "__main__":
    main()
