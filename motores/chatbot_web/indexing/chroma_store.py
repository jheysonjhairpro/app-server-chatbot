import hashlib
import json
from storage.state import now


def document_id(url):
    return hashlib.sha256(url.encode()).hexdigest()


class ChromaStore:
    def __init__(self, settings):
        import chromadb

        self.settings = settings
        self.client = chromadb.PersistentClient(path=str(settings.chroma_path))
        self.collection = None

    def ensure(self, info):
        identity = f"{info['model']}:{info['digest']}:{info['dimension']}"
        try:
            collection = self.client.get_collection(
                self.settings.web_collection, embedding_function=None
            )
        except Exception as exc:
            if exc.__class__.__name__ not in {
                "NotFoundError",
                "InvalidCollectionException",
            }:
                raise
            collection = self.client.create_collection(
                self.settings.web_collection,
                embedding_function=None,
                metadata={
                    "embedding_identity": identity,
                    "dimension": info["dimension"],
                    "hnsw:space": "cosine",
                },
            )
        metadata = collection.metadata or {}
        if (
            metadata.get("embedding_identity") != identity
            or metadata.get("dimension") != info["dimension"]
            or metadata.get("hnsw:space") != "cosine"
        ):
            raise RuntimeError(
                "Incompatible unamba_web collection; migrate manually to preserve data"
            )
        self.collection = collection
        return identity

    def replace(self, page, chunks, vectors, hash_value):
        if len(chunks) != len(vectors):
            raise ValueError("Chunk/vector mismatch")
        doc = document_id(page["url"])
        ids = [f"{doc}:{i}" for i in range(len(chunks))]
        old = self.collection.get(where={"document_id": doc}, include=[])["ids"]
        metadatas = []
        for i, chunk in enumerate(chunks):
            meta = {
                "document_id": doc,
                "url": page["url"],
                "titulo": page["titulo"],
                "portal": page["portal"],
                "categoria": page["categorias"][0],
                "categorias": json.dumps(page["categorias"], ensure_ascii=False),
                "seccion": chunk["seccion"],
                "tipo": "html",
                "fecha_indexacion": now(),
                "content_hash": hash_value,
                "chunk_index": i,
            }
            if page["fecha_publicacion"]:
                meta["fecha_publicacion"] = page["fecha_publicacion"]
            # Chroma primitive metadata supports exact secondary-category filters.
            for cat in page["categorias"]:
                meta["cat_" + hashlib.sha1(cat.encode()).hexdigest()[:12]] = True
            metadatas.append(meta)
        for start in range(0, len(ids), 128):
            self.collection.upsert(
                ids=ids[start : start + 128],
                embeddings=vectors[start : start + 128],
                documents=[c["texto"] for c in chunks[start : start + 128]],
                metadatas=metadatas[start : start + 128],
            )
        obsolete = list(set(old) - set(ids))
        if obsolete:
            self.collection.delete(ids=obsolete)

    def delete(self, url):
        self.collection.delete(where={"document_id": document_id(url)})

    def query(self, vector, k, category=None, document_ids=None):
        if document_ids is not None and not document_ids:
            return []
        if not self.collection or not self.collection.count():
            return []
        kwargs = {}
        if category:
            kwargs["where"] = {
                "cat_" + hashlib.sha1(category.encode()).hexdigest()[:12]: True
            }
        if document_ids:
            match = {"document_id": {"$in": document_ids}}
            kwargs["where"] = (
                {"$and": [kwargs["where"], match]} if "where" in kwargs else match
            )
        result = self.collection.query(
            query_embeddings=[vector],
            n_results=min(k, self.collection.count()),
            include=["documents", "metadatas", "distances"],
            **kwargs,
        )
        return [
            {"id": id, "texto": text, "metadata": meta, "similarity": 1 - distance}
            for id, text, meta, distance in zip(
                result["ids"][0],
                result["documents"][0],
                result["metadatas"][0],
                result["distances"][0],
            )
        ]
