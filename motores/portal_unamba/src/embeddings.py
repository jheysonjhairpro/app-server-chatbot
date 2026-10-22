"""Fábrica del modelo de embeddings compartida por indexador y motor."""

from llama_index.embeddings.ollama import OllamaEmbedding

from motores.portal_unamba.src.config import EMBED_MODEL, OLLAMA_BASE_URL


def build_embedding() -> OllamaEmbedding:
    return OllamaEmbedding(model_name=EMBED_MODEL, base_url=OLLAMA_BASE_URL)
