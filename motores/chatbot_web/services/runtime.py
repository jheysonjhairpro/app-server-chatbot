"""Preparación y cierre de los recursos del chatbot web."""

import asyncio

from indexing.chroma_store import ChromaStore
from rag.ollama_client import Ollama
from rag.retrieval import RAG
from storage.state import State


class Services:
    def __init__(self, settings):
        self.settings = settings
        self.state = State(settings.state_path)
        self.ollama = Ollama(settings)
        self.store = ChromaStore(settings)
        self.lock = asyncio.Lock()
        self.prepare_lock = asyncio.Lock()
        self.prepared = False
        self.rag = RAG(settings, self.state, self.store, self.ollama, self.lock)

    async def prepare(self):
        async with self.prepare_lock:
            self.prepared = False
            info = await self.ollama.check()
            await asyncio.to_thread(self.store.ensure, info)
            self.prepared = True
            return info

    async def ready(self):
        if not self.prepared:
            await self.prepare()

    async def health(self):
        try:
            info = await self.prepare()
            fragments = await asyncio.to_thread(self.store.collection.count)
            return {
                "ok": bool(info.get("chat_available")),
                "ollama": info,
                "chroma": {
                    "coleccion": self.settings.web_collection,
                    "version": self.state.version,
                    "fragmentos": fragments,
                },
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def close(self):
        await self.ollama.close()
        self.state.close()
