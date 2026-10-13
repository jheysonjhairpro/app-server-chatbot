"""Consultas desde la terminal a la API web RAG, sin abrir bases ni lanzar rastreo."""

import argparse
import json
import sys
import time

import httpx


class QueryError(Exception):
    pass


def sse_events(lines):
    """Decode SSE events, including multiline data and an unterminated last event."""
    data = []
    for line in lines:
        if not line:
            if data:
                yield json.loads("\n".join(data))
                data = []
        elif line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
    if data:
        yield json.loads("\n".join(data))


def check_status(response):
    if response.is_success:
        return
    response.read()
    try:
        detail = response.json().get("detail", response.text)
    except (ValueError, AttributeError):
        detail = response.text
    if not isinstance(detail, str):
        detail = json.dumps(detail, ensure_ascii=False)
    raise QueryError(
        f"El servidor respondió HTTP {response.status_code}: {detail[:1000]}"
    )


def show_sources(sources):
    seen = set()
    if sources:
        print("\nFuentes oficiales:")
    for source in sources:
        url = source.get("url")
        if url and url not in seen:
            seen.add(url)
            print(f"- {source.get('titulo', 'Página oficial')}\n  {url}")


def ask(client, question, search_only=False, metrics=False):
    start = time.perf_counter()
    if search_only:
        response = client.post("/rag/buscar", json={"pregunta": question})
        check_status(response)
        result = response.json()
        print("\nContexto recuperado (sin generación):\n")
        print(result.get("contexto") or "No se encontró contexto oficial suficiente.")
        if metrics:
            print("\nMétricas del servidor:")
            print(json.dumps(result.get("metricas", {}), ensure_ascii=False, indent=2))
        print(f"\nTiempo observado: {time.perf_counter() - start:.2f} s")
        return result

    print("Buscando en las páginas web indexadas...", flush=True)
    first_token = None
    parts = []
    sources = []
    final = None
    with client.stream(
        "POST",
        "/rag/preguntar",
        json={"pregunta": question, "streaming": True},
        headers={"Accept": "text/event-stream"},
    ) as response:
        check_status(response)
        for event in sse_events(response.iter_lines()):
            kind = event.get("tipo")
            if kind == "fuentes":
                sources = event.get("fuentes", [])
            elif kind == "token":
                text = event.get("texto", "")
                if text:
                    if first_token is None:
                        first_token = time.perf_counter() - start
                        print("\nRespuesta:\n", end="", flush=True)
                    parts.append(text)
                    print(text, end="", flush=True)
            elif kind == "error":
                raise QueryError(
                    event.get("detalle", "Error del servidor al responder.")
                )
            elif kind == "fin":
                final = event
                break

    if final is None:
        raise QueryError(
            "La conexión terminó antes de recibir la respuesta completa. El texto mostrado puede estar incompleto."
        )
    if not parts:
        print("\nRespuesta:\n" + final.get("respuesta", ""), end="", flush=True)
    print()
    show_sources(final.get("fuentes", sources))
    elapsed = time.perf_counter() - start
    timing = f"\nTiempo observado: {elapsed:.2f} s"
    if first_token is not None:
        timing += f" | Primer texto: {first_token:.2f} s"
    server_metrics = final.get("metricas", {})
    if server_metrics.get("cache_respuesta"):
        timing += " | Respuesta desde caché"
    if server_metrics.get("modo") == "enlaces_documentales":
        timing += " | Enlaces del catálogo"
    print(timing)
    if server_metrics.get("motivo_fin") == "length":
        print("La respuesta alcanzó el límite de tokens; puede estar incompleta.")
    if metrics:
        print("\nMétricas del servidor:")
        print(json.dumps(server_metrics, ensure_ascii=False, indent=2))
    return final


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Pregunta al índice web oficial de la UNAMBA."
    )
    parser.add_argument(
        "pregunta",
        nargs="*",
        help="Pregunta entre comillas. Sin pregunta abre una consola de una sola consulta.",
    )
    parser.add_argument(
        "--url", default="http://127.0.0.1:8000", help="URL del servidor FastAPI."
    )
    parser.add_argument(
        "--buscar",
        action="store_true",
        help="Solo recuperar contexto, sin generar una respuesta.",
    )
    parser.add_argument(
        "--metricas",
        action="store_true",
        help="Mostrar latencias y cachés del servidor.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=180,
        help="Máxima espera sin datos, en segundos.",
    )
    parser.add_argument(
        "--interactivo",
        action="store_true",
        help="Permite hacer varias preguntas en una misma ejecución.",
    )
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout debe ser mayor que cero")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    def query(question):
        if not 2 <= len(question) <= 2000:
            print("Escribe una pregunta de entre 2 y 2000 caracteres.", file=sys.stderr)
            return False
        try:
            ask(client, question, args.buscar, args.metricas)
            return True
        except httpx.ConnectError:
            print(
                "\nNo pude conectar con el servidor. Mantén python main.py ejecutándose en otra terminal.",
                file=sys.stderr,
            )
        except httpx.TimeoutException:
            print(
                "\nSe agotó la espera. Comprueba el servidor o aumenta --timeout.",
                file=sys.stderr,
            )
        except (QueryError, httpx.HTTPError, ValueError) as exc:
            print(f"\nError: {exc}", file=sys.stderr)
        return False

    try:
        with httpx.Client(
            base_url=args.url.rstrip("/"),
            timeout=httpx.Timeout(args.timeout, connect=5),
            trust_env=False,
        ) as client:
            if args.pregunta:
                return 0 if query(" ".join(args.pregunta).strip()) else 1
            if not args.interactivo:
                print("UNAMBA - consulta al chatbot web")
                print(
                    "Escribe una pregunta y presiona Enter. El programa terminará después de responder.\n"
                )
                question = input("Tu pregunta: ").strip()
                if not question:
                    print("No se ingresó ninguna pregunta.", file=sys.stderr)
                    return 1
                return 0 if query(question) else 1
            print("UNAMBA - consulta al chatbot web")
            print("Modo interactivo. Escribe salir para terminar.\n")
            while True:
                question = input("Tu pregunta: ").strip()
                if question.casefold() in {"salir", "exit", "quit"}:
                    return 0
                if question:
                    query(question)
                    print()
    except (KeyboardInterrupt, EOFError):
        print("\nConsulta finalizada.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
