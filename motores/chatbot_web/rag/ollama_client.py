import asyncio
import json
import math
import httpx


class Ollama:
    def __init__(self, settings):
        self.settings = settings
        self.client = httpx.AsyncClient(
            base_url=settings.ollama_url, timeout=120, trust_env=False
        )
        self.embed_lock = asyncio.Semaphore(settings.ollama_concurrency)
        self.generate_lock = asyncio.Semaphore(settings.generation_concurrency)
        self.identity = None
        self.digest = None
        self.dimension = None
        self.chat_thinking_capable = False

    async def check(self):
        r = await self.client.get("/api/tags")
        r.raise_for_status()
        models = r.json().get("models", [])
        model = next(
            (m for m in models if m["name"] == self.settings.embedding_model), None
        )
        if not model:
            raise RuntimeError(
                f"Missing model {self.settings.embedding_model}; no automatic download"
            )
        digest = model.get("digest")
        if not digest:
            raise RuntimeError("Ollama did not provide model digest")
        if self.digest and self.digest != digest:
            raise RuntimeError("Embedding model digest changed; migration required")
        self.digest = digest
        vectors = await self.embed(["UNAMBA: validación de dimensión"])
        self.dimension = len(vectors[0])
        self.identity = f"{self.settings.embedding_model}:{digest}:{len(vectors[0])}"
        chat = next((m for m in models if m["name"] == self.settings.chat_model), {})
        self.chat_thinking_capable = "thinking" in chat.get(
            "capabilities", []
        ) or self.settings.chat_model.startswith("qwen3:")
        return {
            "model": self.settings.embedding_model,
            "digest": digest,
            "dimension": len(vectors[0]),
            "chat_available": bool(chat),
            "chat_model": self.settings.chat_model,
        }

    async def embed(self, texts, query=False):
        # Qwen's instructed query format is used only for searches.
        inputs = (
            [
                "Instruct: Recuperar información oficial de la UNAMBA relevante para la pregunta.\nQuery: "
                + t
                for t in texts
            ]
            if query
            else texts
        )
        async with self.embed_lock:
            if self.digest:
                tags = await self.client.get("/api/tags")
                tags.raise_for_status()
                installed = next(
                    (
                        m
                        for m in tags.json().get("models", [])
                        if m["name"] == self.settings.embedding_model
                    ),
                    {},
                )
                if installed.get("digest") != self.digest:
                    raise RuntimeError("Embedding model changed during execution")
            r = await self.client.post(
                "/api/embed",
                json={
                    "model": self.settings.embedding_model,
                    "input": inputs,
                    "truncate": False,
                    "keep_alive": "10m",
                },
            )
            r.raise_for_status()
            vectors = r.json()["embeddings"]
        if len(vectors) != len(texts) or not vectors or not vectors[0]:
            raise ValueError("Invalid embedding batch")
        dimension = len(vectors[0])
        if self.dimension and dimension != self.dimension:
            raise ValueError("Embedding dimension changed during execution")
        if any(
            len(v) != dimension or not all(math.isfinite(n) for n in v) or not any(v)
            for v in vectors
        ):
            raise ValueError("Invalid embedding vector")
        return vectors

    async def stream(self, messages):
        if not self.settings.chat_model:
            raise RuntimeError("CHAT_MODEL must name an installed generative model")
        async with self.generate_lock:
            body = {
                "model": self.settings.chat_model,
                "messages": messages,
                "stream": True,
                "options": {
                    "temperature": self.settings.temperature,
                    "num_predict": self.settings.max_tokens,
                    "repeat_penalty": 1.1,
                    "repeat_last_n": 128,
                },
                "keep_alive": "10m",
            }
            if self.chat_thinking_capable:
                body["think"] = self.settings.chat_think
            async with self.client.stream("POST", "/api/chat", json=body) as r:
                r.raise_for_status()
                async for line in r.aiter_lines():
                    if line:
                        event = json.loads(line)
                        if event.get("error"):
                            raise RuntimeError(event["error"])
                        yield event

    async def close(self):
        await self.client.aclose()
