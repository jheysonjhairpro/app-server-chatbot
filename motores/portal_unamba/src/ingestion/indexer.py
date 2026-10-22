"""Crea/reconstruye un índice persistente de ChromaDB directamente desde los documentos extraídos."""

import argparse
import logging
import re
import uuid
from pathlib import Path
from urllib.parse import unquote, urlsplit, parse_qsl

import chromadb
from llama_index.core import SimpleDirectoryReader, StorageContext, VectorStoreIndex
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.utils import get_tokenizer
from llama_index.vector_stores.chroma import ChromaVectorStore

from motores.portal_unamba.src.config import (
    CHROMA_COLLECTION,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    RAW_DATA_DIR,
    EMBED_MODEL,
    VECTOR_STORE_DIR,
)
from motores.portal_unamba.src.embeddings import build_embedding
from motores.portal_unamba.src.health import ensure_ollama_ready
from motores.portal_unamba.src.ingestion.markdown_headers import markdown_header_nodes

logger = logging.getLogger(__name__)

NOISE_KEYS = ["file_path", "file_name", "file_type", "file_size", "creation_date",
              "last_modified_date", "last_accessed_date"]


def _collection_names(client: chromadb.ClientAPI) -> set[str]:
    # chromadb < 0.6 devuelve objetos Collection; >= 0.6 devuelve solo nombres.
    return {getattr(item, "name", item) for item in client.list_collections()}


def _drop_leftovers(client: chromadb.ClientAPI) -> None:
    """Recupera un intercambio interrumpido antes de retirar temporales."""
    prefix = CHROMA_COLLECTION[:32]
    names = _collection_names(client)
    backups = sorted(name for name in names if name.startswith(f"{prefix}__backup_"))
    if CHROMA_COLLECTION not in names and backups:
        if len(backups) != 1:
            raise RuntimeError("Hay varios respaldos y falta el índice activo; se conservan para recuperación manual.")
        client.get_collection(backups[0]).modify(name=CHROMA_COLLECTION)
        logger.warning("Índice recuperado desde %s", backups[0])
    for name in _collection_names(client):
        if name.startswith((f"{prefix}__stage_", f"{prefix}__backup_")):
            try:
                client.delete_collection(name)
            except Exception as exc:
                logger.warning("No pude borrar la colección temporal %s: %s", name, exc)


def _prepare_documents(documents, input_dir: Path) -> None:
    for document in documents:
        raw_path = document.metadata.get("file_path") or document.metadata.get("file_name") or "documento"
        try:
            source = Path(raw_path).resolve().relative_to(input_dir).as_posix()
        except ValueError:
            source = Path(raw_path).name
        url = re.search(r"^\s*(?:url|source|fuente)\s*:\s*(https?://\S+)", document.text, re.I | re.M)
        source_url = url.group(1).rstrip(')>') if url else ""
        explicit_title = re.search(r"^title:\s*(.+)$", document.text, re.M)
        # El título procede de la página, no del primer formulario o anuncio.
        # Los encabezados se conservan como información de cada sección.
        if explicit_title:
            title = explicit_title.group(1).strip()
        elif source_url:
            parsed = urlsplit(source_url)
            path = unquote(parsed.path).strip("/")
            label = path.replace("/", " — ").replace("-", " ").replace("_", " ")
            identity = " & ".join(f"{key}={value}" for key, value in parse_qsl(parsed.query))
            title = f"{parsed.hostname}" + (f" — {label}" if label else "") + (f" — {identity}" if identity else "")
        else:
            title = Path(source).stem.replace("_", " ").replace("-", " ")
        document.id_ = source
        document.metadata["source_file"] = source
        document.metadata["titulo"] = title[:240]
        document.metadata["origen"] = source_url or source
        if source_url:
            document.metadata["source_url"] = source_url
        for key in NOISE_KEYS:
            document.metadata.pop(key, None)
        document.excluded_embed_metadata_keys = ["source_file", "source_url"]
        document.excluded_llm_metadata_keys = []


def _build_nodes(documents):
    nodes = markdown_header_nodes(documents)
    tokenizer = get_tokenizer()
    metadata_budget = max(0, min(256, (CHUNK_SIZE - CHUNK_OVERLAP) // 3))
    for node in nodes:
        node.metadata["section_id"] = node.node_id
        # La procedencia se conserva íntegra, pero no consume el presupuesto del
        # fragmento. Solo un contexto breve y medido se utiliza en los embeddings.
        context = '\n'.join(
            f'{key}: {node.metadata[key]}' for key in ('header_path', 'titulo', 'origen')
            if node.metadata.get(key)
        )
        low, high = 0, len(context)
        while low < high:
            mid = (low + high + 1) // 2
            if len(tokenizer('contexto: ' + context[:mid])) <= metadata_budget:
                low = mid
            else:
                high = mid - 1
        if low:
            node.metadata['contexto'] = context[:low]
        node.excluded_embed_metadata_keys = [key for key in node.metadata if key != 'contexto']
        # SentenceSplitter toma el mayor de los metadatos EMBED y LLM. Durante
        # esta etapa no debe contar URL, rutas ni otros datos solo de referencia.
        node.excluded_llm_metadata_keys = list(node.metadata)
    # División local: evita descargas NLTK y conflictos de caché/redirección de rutas en Windows.
    splitter = SentenceSplitter(
        chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP,
        chunking_tokenizer_fn=lambda text: re.split(r"(?<=[.!?])(?=\s)", text),
    )
    nodes = splitter.get_nodes_from_documents(nodes, show_progress=True)

    positions = {}
    for node in nodes:
        source = str(node.metadata.get("source_file", ""))
        node.metadata["chunk_index"] = positions.get(source, 0)
        positions[source] = node.metadata["chunk_index"] + 1
        node.excluded_embed_metadata_keys = [key for key in node.metadata if key != 'contexto']
        node.excluded_llm_metadata_keys = []
    # Se conservan también fragmentos cortos, menús y repeticiones.
    return nodes


def _publish_collection(client, collection):
    """Intercambia la colección preparada y revierte si falla su publicación."""
    backup_name = f"{CHROMA_COLLECTION[:32]}__backup_{uuid.uuid4().hex[:12]}"
    had_previous_index = CHROMA_COLLECTION in _collection_names(client)
    if had_previous_index:
        client.get_collection(CHROMA_COLLECTION).modify(name=backup_name)
    try:
        collection.modify(name=CHROMA_COLLECTION)
    except Exception:
        if had_previous_index:
            client.get_collection(backup_name).modify(name=CHROMA_COLLECTION)
        raise
    if had_previous_index:
        try:
            client.delete_collection(backup_name)
        except Exception as exc:
            logger.warning('El índice nuevo quedó activo, pero no se pudo retirar el respaldo %s: %s', backup_name, exc)


def build_empty_index(persist_dir: Path = VECTOR_STORE_DIR) -> int:
    """Publica un índice vacío únicamente por petición explícita del panel."""
    persist_dir = persist_dir.expanduser().resolve()
    persist_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(persist_dir), settings=chromadb.Settings(anonymized_telemetry=False))
    _drop_leftovers(client)
    staging = f"{CHROMA_COLLECTION[:32]}__stage_{uuid.uuid4().hex[:12]}"
    collection = client.create_collection(staging, metadata={
        'hnsw:space': 'cosine', 'embedding_model': EMBED_MODEL,
        'chunk_size': CHUNK_SIZE, 'chunk_overlap': CHUNK_OVERLAP,
        'schema_version': 3, 'chunking_strategy': 'markdown_headers',
    })
    try:
        _publish_collection(client, collection)
    except Exception:
        if staging in _collection_names(client):
            client.delete_collection(staging)
        raise
    return 0


def build_index(input_dir: Path = RAW_DATA_DIR, persist_dir: Path = VECTOR_STORE_DIR) -> int:
    input_dir = input_dir.expanduser().resolve()
    persist_dir = persist_dir.expanduser().resolve()
    if not input_dir.is_dir():
        raise FileNotFoundError(f"No existe la carpeta de documentos: {input_dir}")
    ensure_ollama_ready([EMBED_MODEL])

    documents = SimpleDirectoryReader(
        input_dir=str(input_dir),
        recursive=True,
        required_exts=[".md", ".txt"],
        filename_as_id=True,
    ).load_data()
    if not documents:
        raise ValueError(f"No hay documentos .md/.txt para indexar en {input_dir}")
    _prepare_documents(documents, input_dir)

    nodes = _build_nodes(documents)
    if not nodes:
        raise ValueError("Los documentos no produjeron fragmentos indexables.")

    persist_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(persist_dir), settings=chromadb.Settings(anonymized_telemetry=False))
    _drop_leftovers(client)
    staging_name = f"{CHROMA_COLLECTION[:32]}__stage_{uuid.uuid4().hex[:12]}"
    collection = client.create_collection(staging_name, metadata={
        "hnsw:space": "cosine", "embedding_model": EMBED_MODEL,
        "chunk_size": CHUNK_SIZE, "chunk_overlap": CHUNK_OVERLAP, "schema_version": 3,
        "chunking_strategy": "markdown_headers",
    })
    storage_context = StorageContext.from_defaults(vector_store=ChromaVectorStore(chroma_collection=collection))
    try:
        VectorStoreIndex(
            nodes,
            storage_context=storage_context,
            embed_model=build_embedding(),
            show_progress=True,
        )
        if collection.count() == 0:
            raise RuntimeError("La colección temporal quedó vacía; se conservó el índice anterior.")

        _publish_collection(client, collection)
    except Exception:
        if staging_name in _collection_names(client):
            client.delete_collection(staging_name)
        raise
    return len(nodes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=RAW_DATA_DIR)
    parser.add_argument("--persist-dir", type=Path, default=VECTOR_STORE_DIR)
    args = parser.parse_args()
    try:
        count = build_index(args.input_dir, args.persist_dir)
    except Exception as exc:
        raise SystemExit(f"No se pudo indexar: {exc}") from exc
    print(f"Índice actualizado: {count} fragmentos en {args.persist_dir.resolve()}")


if __name__ == "__main__":
    main()
