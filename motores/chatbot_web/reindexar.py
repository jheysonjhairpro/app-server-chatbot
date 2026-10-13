"""Update the chatbot's own index without copying databases between projects."""

import argparse
import asyncio
import json
import logging
import uuid
from pathlib import Path

from indexing.settings import Settings
from crawler.http import Fetcher
from crawler.spider import Spider
from crawler.url_manager import URLPolicy
from indexing.chroma_store import ChromaStore
from indexing.incremental import Indexer
from rag.ollama_client import Ollama
from storage.state import State


async def run(args):
    settings = Settings()
    for name in ("max_pages", "max_seconds"):
        value = getattr(args, name)
        if value is not None:
            setattr(settings, name, value)
    policy = URLPolicy(settings)
    if not all(policy.allowed(url) for url in settings.seeds):
        raise ValueError("Hay una semilla fuera de los dominios autorizados.")
    if args.verificar:
        print(
            json.dumps(
                {
                    "semillas": settings.seeds,
                    "dominios": settings.allowed_domains,
                    "sqlite": str(settings.state_path.resolve()),
                    "chroma": str(settings.chroma_path.resolve()),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    try:
        state = State(settings.state_path)
    except RuntimeError as exc:
        raise RuntimeError(
            "Cierra el servidor del chatbot con Ctrl+C antes de reindexar. Luego ejecuta reindexar.bat nuevamente."
        ) from exc
    ollama = None
    fetcher = None
    try:
        ollama = Ollama(settings)
        fetcher = Fetcher(settings, policy)
        info = await ollama.check()
        store = ChromaStore(settings)
        await asyncio.to_thread(store.ensure, info)
        state.ensure_pipeline("html-links-v4-scoped-headings")
        indexer = Indexer(settings, state, store, ollama, asyncio.Lock())
        spider = Spider(settings, state, fetcher, indexer)
        print(
            f"Actualizando {len(settings.seeds)} fuentes. Limites: {settings.max_pages} paginas / {settings.max_seconds} segundos.",
            flush=True,
        )
        report = await spider.run(str(uuid.uuid4()), "incremental", args.resume)
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"Informe: {destination.resolve()}")
    finally:
        if fetcher is not None:
            await fetcher.close()
        if ollama is not None:
            await ollama.close()
        state.close()


def main():
    import os

    os.chdir(Path(__file__).resolve().parent)
    parser = argparse.ArgumentParser(
        description="Actualizar el indice de UNAMBA_CHATBOT_WEB"
    )
    parser.add_argument(
        "--resume", action="store_true", help="Continuar las paginas pendientes"
    )
    parser.add_argument(
        "--verificar",
        action="store_true",
        help="Mostrar configuracion sin rastrear ni abrir el indice",
    )
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--max-seconds", type=int)
    parser.add_argument("--output", default="reports/actualizacion.json")
    args = parser.parse_args()
    for value in (args.max_pages, args.max_seconds):
        if value is not None and value < 1:
            parser.error("Los limites deben ser positivos")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nInterrumpido. Puedes continuar con reindexar.bat --resume.")
        return 130
    except Exception as exc:
        print(f"Error: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
