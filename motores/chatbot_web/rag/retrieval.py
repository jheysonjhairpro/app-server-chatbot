import asyncio
import copy
import hashlib
import json
import time
from rag.document_links import document_answer
from indexing.classifier import query_category
from rag.cache import TTLCache
from rag.context_builder import select
from rag.prompts import SYSTEM_PROMPT


class RAG:
    def __init__(self, settings, state, store, ollama, lock):
        self.settings, self.state, self.store, self.ollama, self.lock = (
            settings,
            state,
            store,
            ollama,
            lock,
        )
        self.embeddings = TTLCache(settings.cache_size, settings.cache_ttl * 12)
        self.retrieval = TTLCache(settings.cache_size, settings.cache_ttl)
        self.answers = TTLCache(settings.cache_size, settings.cache_ttl)
        self.singleflight = {}

    def normalized(self, q):
        import unicodedata

        return " ".join(unicodedata.normalize("NFKC", q).casefold().split())

    async def search(self, question, category=None):
        key = (self.normalized(question), category, self.ollama.identity)
        # A bounded keyed lock prevents duplicate embedding/retrieval work for identical questions.
        entry = self.singleflight.setdefault(key, [asyncio.Lock(), 0])
        entry[1] += 1
        try:
            async with entry[0]:
                return await self._search(question, category, key)
        finally:
            entry[1] -= 1
            if entry[1] == 0:
                self.singleflight.pop(key, None)

    async def _search(self, question, category, key):
        start = time.perf_counter()
        async with self.lock:
            version = self.state.version
            cached = self.retrieval.get((key, version))
            if cached is not None:
                result = copy.deepcopy(cached)
                result["metricas"] = {
                    "embedding_ms": 0,
                    "recuperacion_ms": 0,
                    "seleccion_ms": 0,
                    "total_ms": (time.perf_counter() - start) * 1000,
                    "cache_recuperacion": True,
                }
                return result
        embedding_key = (self.normalized(question), self.ollama.identity)
        vector = self.embeddings.get(embedding_key)
        embed_start = time.perf_counter()
        embedding_cached = vector is not None
        if vector is None:
            vector = (await self.ollama.embed([question], query=True))[0]
            self.embeddings.put(embedding_key, vector)
        embed_ms = (time.perf_counter() - embed_start) * 1000
        cat = category or query_category(question)
        retrieval_start = time.perf_counter()
        async with self.lock:
            version = self.state.version
            from indexing.chroma_store import document_id

            # Restrict vector retrieval to documents present in the saved catalog.
            allowed = {document_id(url) for url in self.state.indexed_urls()}
            candidates = await asyncio.to_thread(
                self.store.query,
                vector,
                self.settings.top_k_candidatos,
                cat,
                list(allowed),
            )
            if (
                cat
                and not category
                and not any(
                    c["similarity"] >= self.settings.min_similarity for c in candidates
                )
            ):
                cat = None
                candidates = await asyncio.to_thread(
                    self.store.query,
                    vector,
                    self.settings.top_k_candidatos,
                    cat,
                    list(allowed),
                )
            # Supplement explicit title matches, then rank their vectors with Chroma.
            matching = [
                document_id(url)
                for url in self.state.title_matches(question)
                if document_id(url) in allowed
            ]
            if matching:
                extra = await asyncio.to_thread(
                    self.store.query,
                    vector,
                    self.settings.top_k_candidatos,
                    cat,
                    matching,
                )
                candidates = list({c["id"]: c for c in candidates + extra}.values())
            retrieval_ms = (time.perf_counter() - retrieval_start) * 1000
            selection_start = time.perf_counter()
            context, chosen = select(question, candidates, self.settings)
            result = {
                "pregunta": question,
                "contexto": context,
                "fragmentos": chosen,
                "version": version,
                "categoria_filtro": cat,
                "metricas": {
                    "embedding_ms": embed_ms,
                    "recuperacion_ms": retrieval_ms,
                    "seleccion_ms": (time.perf_counter() - selection_start) * 1000,
                    "total_ms": (time.perf_counter() - start) * 1000,
                    "cache_embedding": embedding_cached,
                    "cache_recuperacion": False,
                },
            }
            self.retrieval.put((key, version), copy.deepcopy(result))
            return result

    async def answer_events(self, question, category=None):
        start = time.perf_counter()
        found = await self.search(question, category)
        sources = list(
            {
                c["metadata"]["url"]: {
                    "titulo": c["metadata"]["titulo"],
                    "url": c["metadata"]["url"],
                }
                for c in found["fragmentos"]
            }.values()
        )
        yield {
            "tipo": "fuentes",
            "fuentes": sources,
            "metricas": found["metricas"],
            "version": found["version"],
        }
        config = (
            self.settings.chat_model,
            self.settings.temperature,
            self.settings.max_tokens,
            self.settings.chat_think,
            self.settings.direct_link_answers,
            SYSTEM_PROMPT,
        )
        key = hashlib.sha256(
            json.dumps(
                [
                    self.normalized(question),
                    found["contexto"],
                    found["version"],
                    config,
                ],
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        cached = self.answers.get(key)
        if cached:
            yield {"tipo": "token", "texto": cached["respuesta"]}
            yield {
                "tipo": "fin",
                "respuesta": cached["respuesta"],
                "fuentes": sources,
                "metricas": {
                    **found["metricas"],
                    "cache_respuesta": True,
                    "ttft_ms": (time.perf_counter() - start) * 1000,
                    "duracion_total_ms": (time.perf_counter() - start) * 1000,
                    "tokens_entrada": 0,
                    "tokens_salida": 0,
                    "tokens_respuesta_original": cached["tokens_salida"],
                },
            }
            return
        direct = (
            self.document_answer(question, [s["url"] for s in sources])
            if self.settings.direct_link_answers
            else None
        )
        if direct:
            self.answers.put(key, {"respuesta": direct, "tokens_salida": 0})
            yield {"tipo": "token", "texto": direct}
            yield {
                "tipo": "fin",
                "respuesta": direct,
                "fuentes": sources,
                "metricas": {
                    **found["metricas"],
                    "cache_respuesta": False,
                    "ttft_ms": (time.perf_counter() - start) * 1000,
                    "duracion_total_ms": (time.perf_counter() - start) * 1000,
                    "tokens_entrada": 0,
                    "tokens_salida": 0,
                    "modo": "enlaces_documentales",
                },
            }
            return
        if not found["contexto"]:
            answer = "No encontré información oficial suficiente en las páginas web indexadas para responder esta pregunta."
            yield {"tipo": "token", "texto": answer}
            yield {
                "tipo": "fin",
                "respuesta": answer,
                "fuentes": sources,
                "metricas": {
                    **found["metricas"],
                    "cache_respuesta": False,
                    "ttft_ms": (time.perf_counter() - start) * 1000,
                    "duracion_total_ms": (time.perf_counter() - start) * 1000,
                    "tokens_entrada": 0,
                    "tokens_salida": 0,
                },
            }
            return
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {"contexto_oficial": found["contexto"], "pregunta": question},
                    ensure_ascii=False,
                ),
            },
        ]
        parts = []
        first = None
        last = {}
        async for event in self.ollama.stream(messages):
            text = event.get("message", {}).get("content", "")
            if text:
                if first is None:
                    first = (time.perf_counter() - start) * 1000
                parts.append(text)
                yield {"tipo": "token", "texto": text}
            last = event
        answer = "".join(parts)
        if not last.get("done"):
            raise RuntimeError("Ollama stream ended without completion")
        tokens = last.get("eval_count", 0)
        if last.get("done_reason") != "length":
            self.answers.put(key, {"respuesta": answer, "tokens_salida": tokens})
        yield {
            "tipo": "fin",
            "respuesta": answer,
            "fuentes": sources,
            "metricas": {
                **found["metricas"],
                "cache_respuesta": False,
                "ttft_ms": first,
                "duracion_total_ms": (time.perf_counter() - start) * 1000,
                "tokens_entrada": last.get("prompt_eval_count", 0),
                "tokens_salida": tokens,
                "motivo_fin": last.get("done_reason"),
            },
        }

    def cache_stats(self):
        return {
            "embeddings": self.embeddings.stats(),
            "recuperacion": self.retrieval.stats(),
            "respuestas": self.answers.stats(),
        }

    def document_answer(self, question, urls):
        return document_answer(self.state, question, urls)
