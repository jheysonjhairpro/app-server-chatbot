import asyncio
from indexing.cleaner import content_hash
from indexing.chunker import chunk_page
from indexing.embeddings import embed_chunks


class Indexer:
    def __init__(self, settings, state, store, ollama, lock):
        self.settings, self.state, self.store, self.ollama, self.lock = (
            settings,
            state,
            store,
            ollama,
            lock,
        )

    async def index(self, url, page, links, headers):
        # Include titles, dates, labels and document catalogue in normalized hash.
        import json

        def hash_page():
            return content_hash(
                json.dumps(
                    {
                        "title": page["titulo"],
                        "text": page["texto"],
                        "date": page["fecha_publicacion"],
                        "categories": page["categorias"],
                        "links": links,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )

        hash_value = await asyncio.to_thread(hash_page)
        old = self.state.page(url)
        if old and old["hash"] == hash_value:
            self.state.save(
                url,
                page,
                hash_value,
                links,
                headers.get("etag"),
                headers.get("last-modified"),
            )
            return {"sin_cambios": 1, "fragmentos": 0, "embeddings": 0}
        chunks = await asyncio.to_thread(
            chunk_page, page, self.settings.chunk_size, self.settings.chunk_overlap
        )
        vectors = (
            await embed_chunks(self.ollama, chunks, self.settings.embed_batch_size)
            if chunks
            else []
        )
        async with self.lock:
            # Invalidate before mutation, including interrupted partial writes.
            self.state.bump()
            self.state.dirty(url)
            operation = asyncio.create_task(
                asyncio.to_thread(self.store.replace, page, chunks, vectors, hash_value)
            )
            try:
                await asyncio.shield(operation)
            except asyncio.CancelledError:
                await operation
                raise
            self.state.save(
                url,
                page,
                hash_value,
                links,
                headers.get("etag"),
                headers.get("last-modified"),
            )
        return {"sin_cambios": 0, "fragmentos": len(chunks), "embeddings": len(vectors)}

    async def remove(self, url):
        async with self.lock:
            self.state.bump()
            self.state.dirty(url)
            await asyncio.to_thread(self.store.delete, url)
            self.state.mark(
                url, "gone", "HTTP 404/410 confirmed; obsolete fragments removed"
            )
