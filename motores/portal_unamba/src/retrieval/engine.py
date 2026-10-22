"""Motor RAG híbrido: vectorial + BM25, reranking opcional, memoria de conversación y fuentes."""

import json
import logging
import re
from time import perf_counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

import chromadb
from llama_index.core import VectorStoreIndex
from llama_index.core.schema import NodeWithScore, QueryBundle, TextNode
from llama_index.core.utils import get_tokenizer
from llama_index.llms.ollama import Ollama
from llama_index.vector_stores.chroma import ChromaVectorStore
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from motores.portal_unamba.src.bot.prompts import ABSTENTION_ANSWER, STRICT_QA_PROMPT, QUERY_PLAN_PROMPT, COVERAGE_PROMPT, REVIEW_LIST_PROMPT
from motores.portal_unamba.src.bot.smalltalk import small_talk_reply
from motores.portal_unamba.src.config import (
    FAST_MODE, ANSWER_CACHE_SIZE, ANSWER_CACHE_TTL, STREAM_RESPONSES,
    BM25_TOP_K,
    ENABLE_QUERY_PLANNING,
    REVIEW_LISTS,
    MAX_CONTEXT_TOKENS,
    CONTEXT_NEIGHBORS,
    LOG_RETRIEVAL,
    CHROMA_COLLECTION,
    CONTEXT_TOP_N,
    EMBED_MODEL,
    ENABLE_HYBRID,
    ENABLE_RERANKER,
    HISTORY_TURNS,
    LLM_MODEL,
    LOG_UNANSWERED,
    LOGS_DIR,
    NUM_PREDICT,
    OLLAMA_BASE_URL,
    OLLAMA_CONTEXT_SIZE,
    OLLAMA_REQUEST_TIMEOUT,
    QUERY_INSTRUCTION,
    RERANK_MODEL,
    SIMILARITY_TOP_K,
    TEMPERATURE,
    VECTOR_STORE_DIR,
)
from motores.portal_unamba.src.embeddings import build_embedding
from motores.portal_unamba.src.health import ensure_ollama_ready
from motores.portal_unamba.src.retrieval.bm25 import BM25Index, normalize, tokenize
from motores.portal_unamba.src.retrieval.queries import search_queries, question_parts, safe_queries, content_terms, asks_for_list
from motores.portal_unamba.src.retrieval.context import redundant, merge_neighbors, cite_exact_headings, complete_heading_list, faculty_career_list, linked_service_list, linked_masters_list, branch_labels, branch_reference_list, authority_identity_answer
from motores.portal_unamba.src.retrieval.grounding import normalize_citations, evidence_text, needs_grounding, Claim, GroundingReview, GROUNDING_PROMPT, FAST_FACT_PROMPT, reviewed_answer
from motores.portal_unamba.src.retrieval.document_answers import priority_records, school_fact_answer, authority_roster_answer, asks_professional_catalog, professional_catalog_answer, institutional_contact_answer, school_location_answer, vocational_answer
from motores.portal_unamba.src.retrieval.cache import VerifiedAnswerCache
from motores.portal_unamba.src.bot.prompts import _fecha_actual

logger = logging.getLogger(__name__)

RRF_K = 60


@dataclass
class Source:
    file: str
    score: float | None
    excerpt: str
    title: str = ""
    number: int = 0
    url: str = ""


class GroundedResponse(BaseModel):
    """Ollama genera este esquema; el estado no se deduce de frases de la respuesta."""

    model_config = ConfigDict(extra="forbid")
    has_evidence: StrictBool = Field(description="True si el contexto responde al menos una parte de la pregunta sobre la UNAMBA.")
    source_ids: list[int] = Field(description="Identificadores de fuentes que respaldan la respuesta. Vacío si has_evidence es false.")
    answer: str = Field(description="Respuesta en español con citas [n]. Si falta una parte, indícalo sin descartar lo conocido.")



class QueryPlan(BaseModel):
    queries: list[str] = Field(default_factory=list)
    broad: bool = False


class CoverageCheck(BaseModel):
    missing_queries: list[str] = Field(default_factory=list)


@dataclass
class Answer:
    text: str
    sources: list[Source]
    abstained: bool = False
    standalone_question: str = ""


def _build_llm() -> Ollama:
    kwargs: dict[str, Any] = dict(
        model=LLM_MODEL,
        base_url=OLLAMA_BASE_URL,
        temperature=TEMPERATURE,
        context_window=OLLAMA_CONTEXT_SIZE,
        request_timeout=OLLAMA_REQUEST_TIMEOUT,
        additional_kwargs={"num_predict": NUM_PREDICT},
    )
    # Solo los modelos con "modo pensamiento" (qwen3, deepseek-r1...) necesitan desactivarlo.
    if any(tag in LLM_MODEL.lower() for tag in ("qwen3", "deepseek-r1", "gpt-oss")):
        try:
            return Ollama(thinking=False, **kwargs)
        except Exception:
            logger.warning("Esta versión de llama-index-llms-ollama no admite 'thinking'; actualízala.")
    return Ollama(**kwargs)


class RAGEngine:
    def __init__(self, allow_empty: bool = False) -> None:
        self.history: list[tuple[str, str]] = []
        self.has_documents = False
        self.document_count = 0
        if allow_empty and not VECTOR_STORE_DIR.exists():
            return
        client = chromadb.PersistentClient(path=str(VECTOR_STORE_DIR), settings=chromadb.Settings(anonymized_telemetry=False))
        try:
            collection = client.get_collection(CHROMA_COLLECTION)
        except tuple(
            getattr(chromadb.errors, name)
            for name in ("NotFoundError", "InvalidCollectionException")
            if hasattr(chromadb.errors, name)
        ) as exc:
            if allow_empty:
                return
            raise RuntimeError(
                "No existe el índice. Ejecuta primero la extracción y la indexación "
                "(o: iniciar.bat extraer e iniciar.bat indexar)."
            ) from exc
        if collection.count() == 0:
            if allow_empty:
                return
            raise RuntimeError("La colección Chroma está vacía; vuelve a ejecutar la ingesta.")

        indexed_model = (collection.metadata or {}).get("embedding_model")
        if indexed_model and indexed_model != EMBED_MODEL:
            raise RuntimeError(
                f"El índice usa embeddings {indexed_model}, pero .env indica {EMBED_MODEL}. "
                "Ejecuta iniciar.bat indexar para reconstruirlo con el modelo configurado."
            )
        self.document_count = collection.count()
        ensure_ollama_ready([LLM_MODEL, EMBED_MODEL])

        self.llm = _build_llm()
        self.embedding = build_embedding()
        self.index = VectorStoreIndex.from_vector_store(
            ChromaVectorStore(chroma_collection=collection), embed_model=self.embedding
        )
        self.retriever = self.index.as_retriever(similarity_top_k=SIMILARITY_TOP_K)

        self._records: dict[str, tuple[str, dict]] = {}
        self.bm25: BM25Index | None = None
        self._load_lexical_index(collection)
        if not ENABLE_HYBRID:
            self.bm25 = None
        self.query_planning = ENABLE_QUERY_PLANNING and not FAST_MODE
        self.review_lists = REVIEW_LISTS
        self.last_retrieval = {}
        if (collection.metadata or {}).get("schema_version", 1) < 3:
            logger.warning("Reconstruye el índice con iniciar_indexador.bat para activar Markdown Header Chunking con jerarquía completa.")

        self.reranker = None
        if ENABLE_RERANKER:
            from llama_index.core.postprocessor import SentenceTransformerRerank

            self.reranker = SentenceTransformerRerank(model=RERANK_MODEL, top_n=max(CONTEXT_TOP_N * 4, 24), device="cpu")

        self.tokenizer = get_tokenizer()
        empty_prompt = STRICT_QA_PROMPT.format(context_str="", query_str="")
        overhead = len(self.tokenizer(empty_prompt + json.dumps(GroundedResponse.model_json_schema()))) + 256
        self.context_token_budget = OLLAMA_CONTEXT_SIZE - NUM_PREDICT - overhead
        if MAX_CONTEXT_TOKENS > 0:
            self.context_token_budget = min(self.context_token_budget, MAX_CONTEXT_TOKENS)
        if self.context_token_budget < 256:
            raise ValueError("OLLAMA_CONTEXT_SIZE es insuficiente para la respuesta y las instrucciones.")
        self.has_documents = True

    # ---------- recuperación ----------
    def _load_lexical_index(self, collection) -> None:
        ids: list[str] = []
        texts: list[str] = []
        offset, page = 0, 2000
        while True:
            batch = collection.get(include=["documents", "metadatas"], limit=page, offset=offset)
            got = batch.get("ids") or []
            if not got:
                break
            for cid, doc, meta in zip(got, batch["documents"], batch["metadatas"]):
                meta = meta or {}
                self._records[cid] = (doc or "", meta)
                header = " ".join(str(meta.get(k, "")) for k in ("titulo", "header_path"))
                ids.append(cid)
                texts.append(f"{header} {doc or ''}")
            offset += len(got)
            if len(got) < page:
                break
        self.bm25 = BM25Index(ids, texts) if ids else None
        self._by_source = {}
        self._positions = {}
        self._frequency = {}
        for cid, (text, metadata) in self._records.items():
            self._by_source.setdefault(metadata.get("source_file", cid), []).append(cid)
            fingerprint = " ".join(text.split()).lower()
            self._frequency[fingerprint] = self._frequency.get(fingerprint, 0) + 1
        for ids in self._by_source.values():
            ids.sort(key=lambda cid: (self._records[cid][1].get("chunk_index", 0), cid))
            for position, cid in enumerate(ids):
                self._positions[cid] = (ids, position)

    def _node_from_record(self, cid: str):
        doc, meta = self._records[cid]
        try:
            from llama_index.core.vector_stores.utils import metadata_dict_to_node

            node = metadata_dict_to_node(meta)
            # Chroma guarda el texto separado de _node_content (remove_text=True).
            node.set_content(doc)
            return node
        except Exception:
            clean = {k: v for k, v in meta.items() if not k.startswith("_") and "doc_id" not in k}
            return TextNode(id_=cid, text=doc, metadata=clean)

    def _query_bundle(self, question: str) -> QueryBundle:
        if not QUERY_INSTRUCTION:
            return QueryBundle(query_str=question)
        embedding = self.embedding.get_text_embedding(f"Instruct: {QUERY_INSTRUCTION}\nQuery: {question}")
        return QueryBundle(query_str=question, embedding=embedding)

    def _retrieve(self, question: str) -> tuple[list[NodeWithScore], QueryBundle]:
        bundle = QueryBundle(query_str=question)
        try:
            bundle = self._query_bundle(question)
            vector_nodes = list(self.retriever.retrieve(bundle))
        except Exception:
            if self.bm25 is None:
                raise
            logger.exception("Falló la búsqueda vectorial; se intentará búsqueda por palabras.")
            vector_nodes = []
        lexical: list[tuple[str, float]] = []
        if self.bm25 is not None:
            # BM25 tokeniza una sola vez, igual que durante la indexación.
            lexical = self.bm25.search(question, BM25_TOP_K)
        if not lexical:
            return vector_nodes, bundle

        # Reciprocal Rank Fusion: combina ambos rankings sin depender de la escala de sus puntuaciones.
        fused: dict[str, float] = {}
        by_id: dict[str, NodeWithScore] = {}
        for rank, item in enumerate(vector_nodes):
            nid = item.node.node_id
            by_id[nid] = item
            fused[nid] = fused.get(nid, 0.0) + 1.0 / (RRF_K + rank + 1)
        for rank, (cid, _score) in enumerate(lexical):
            if cid not in by_id:
                by_id[cid] = NodeWithScore(node=self._node_from_record(cid), score=None)
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
        ordered = sorted(fused, key=fused.get, reverse=True)
        return [by_id[nid] for nid in ordered], bundle

    def _plan_queries(self, question: str) -> tuple[list[str], bool]:
        queries = search_queries(question)
        broad = asks_for_list(question)
        if getattr(self, "query_planning", False):
            try:
                plan = self.llm.structured_predict(QueryPlan, QUERY_PLAN_PROMPT, question=question)
                queries += safe_queries(question, plan.queries[:4])
                broad = broad or plan.broad
            except Exception as exc:
                logger.warning("Plan de búsqueda no disponible; se conserva la pregunta original: %s", exc)
        return list(dict.fromkeys([question] + queries))[:5], broad

    def _heading_evidence(self, question: str) -> list[NodeWithScore]:
        """Índices de secciones del corpus para cubrir enumeraciones amplias.

        No contiene catálogos de temas: selecciona jerarquías que coinciden
        léxicamente con la pregunta y conserva únicamente títulos existentes.
        """
        terms = content_terms(question)
        groups = {}
        for cid, (_, meta) in getattr(self, '_records', {}).items():
            path = str(meta.get('header_path', ''))
            matched = terms & content_terms(path)
            from urllib.parse import urlsplit
            catalog = asks_professional_catalog(question) and urlsplit(str(meta.get('source_url', ''))).path.rstrip('/') == '/escuelas-profesionales'
            if not catalog and (not path or not terms or len(matched) / len(terms) < .5):
                continue
            source = meta.get('source_file', cid)
            group = groups.setdefault(source, {'meta': meta, 'paths': {}})
            group['paths'].setdefault(path, meta.get('chunk_index', 0))
        evidence = []
        for source, group in groups.items():
            paths = sorted(group['paths'], key=group['paths'].get)
            meta = {key: value for key, value in group['meta'].items() if not key.startswith('_')}
            meta['header_path'] = 'Índice de secciones documentadas'
            text = 'Encabezados originales de esta página (jerarquía, no contenido de enlaces):\n'
            text += '\n'.join('- ' + path for path in paths)
            node = TextNode(id_='outline:' + source, text=text, metadata=meta)
            evidence.append(NodeWithScore(node=node, score=1.0))
        # Los índices con varias secciones cubren primero listas y categorías.
        return sorted(evidence, key=lambda n: n.node.text.count('\n'), reverse=True)[:6]

    def _context_node(self, item: NodeWithScore, number: int) -> NodeWithScore:
        node = item.node.model_copy(deep=True)
        node.metadata["fuente"] = f"[{number}]"
        allowed = {"fuente", "source_url", "titulo", "header_path"}
        node.excluded_llm_metadata_keys = [key for key in node.metadata if key not in allowed]
        return NodeWithScore(node=node, score=item.score)

    def _pack_context(self, ordered: list[NodeWithScore], question: str, limit: int) -> list[NodeWithScore]:
        selected = []
        used = len(self.tokenizer(question))
        fingerprints = set()
        selected_texts = []
        for item in ordered:
            if len(selected) >= limit:
                break
            fingerprint = " ".join(item.node.get_content().split()).lower()
            if not fingerprint or fingerprint in fingerprints:
                continue  # misma evidencia ocupa un solo bloque; originales intactos
            if redundant(item.node.get_content(), selected_texts):
                continue
            candidate = self._context_node(item, len(selected) + 1)
            size = len(self.tokenizer(candidate.node.get_content(metadata_mode="llm"))) + 16
            if used + size > self.context_token_budget:
                continue
            selected.append(candidate)
            fingerprints.add(fingerprint)
            used += size
            selected_texts.append(item.node.get_content())

        # Primero se reserva evidencia de varias fuentes/aspectos; después se
        # amplían los bloques con vecinos de la misma sección, en orden original.
        claimed = {item.node.node_id for item in selected}
        for item in selected:
            relation = getattr(self, "_positions", {}).get(item.node.node_id)
            if not relation or CONTEXT_NEIGHBORS <= 0:
                continue
            ids, position = relation
            section = item.node.metadata.get("section_id")
            if not section:
                continue  # índices anteriores no garantizan el orden
            nearby = ids[max(0, position - CONTEXT_NEIGHBORS):position + CONTEXT_NEIGHBORS + 1]
            nearby = [cid for cid in nearby if self._records[cid][1].get("section_id") == section]
            nearby = [cid for cid in nearby if cid == item.node.node_id or cid not in claimed]
            texts = [self._records[cid][0] for cid in nearby]
            if len(texts) < 2:
                continue
            old_size = len(self.tokenizer(item.node.get_content(metadata_mode="llm")))
            original = item.node.get_content()
            item.node.set_content(merge_neighbors(texts))
            extra = len(self.tokenizer(item.node.get_content(metadata_mode="llm"))) - old_size
            if used + extra <= self.context_token_budget:
                used += extra
                claimed.update(nearby)
            else:
                item.node.set_content(original)
        return selected

    def _select_context(self, question: str, original: str = "") -> list[NodeWithScore]:
        started = perf_counter()
        queries, broad = self._plan_queries(question)
        planning_seconds = perf_counter() - started
        if original and original != question:
            queries = list(dict.fromkeys(queries + [original]))
        by_id, scores, query_hits = {}, {}, []

        def search(query):
            results, _ = self._retrieve(query)
            query_hits.append((query, results))
            for rank, item in enumerate(results):
                nid = item.node.node_id
                by_id.setdefault(nid, item)
                scores[nid] = scores.get(nid, 0) + 1 / (RRF_K + rank + 1)

        def order():
            # Repeticiones conservadas en el índice, con menor prioridad para que
            # los menús compartidos no monopolicen el contexto.
            def relevance(nid):
                fingerprint = " ".join(by_id[nid].node.get_content().split()).lower()
                copies = getattr(self, "_frequency", {}).get(fingerprint, 1)
                return scores[nid] / (1 + 0.25 * max(0, copies - 1) ** 0.5)
            ordered = [by_id[nid] for nid in sorted(scores, key=relevance, reverse=True)]
            if self.reranker is not None and ordered:
                try:
                    ranked = self.reranker.postprocess_nodes(ordered, query_bundle=QueryBundle(query_str=question))
                    if ranked:
                        seen = {n.node.node_id for n in ranked}
                        ordered = ranked + [n for n in ordered if n.node.node_id not in seen]
                except Exception as exc:
                    logger.warning("Reranker no disponible; se mantiene la búsqueda híbrida: %s", exc)
            # Una evidencia por aspecto solicitado antes del ranking global.
            anchors = []
            for query, results in query_hits:
                ranked_ids = {n.node.node_id: rank for rank, n in enumerate(results)}
                candidates = [n for n in ordered if n.node.node_id in ranked_ids]
                first = max(candidates, key=lambda n: relevance(n.node.node_id) / (1 + ranked_ids[n.node.node_id] / 10), default=None)
                if first is not None and all(n.node.node_id != first.node.node_id for n in anchors):
                    anchors.append(first)
            anchored = {n.node.node_id for n in anchors}
            return anchors + [n for n in ordered if n.node.node_id not in anchored]

        for query in queries:
            search(query)
        limit = max(CONTEXT_TOP_N, CONTEXT_TOP_N * 2 if broad else CONTEXT_TOP_N)
        outlines = self._heading_evidence(question) if broad else []
        catalogs = []
        if re.search(r'\b(?:maestrias|mestrias)\b', normalize(question)):
            for cid, (text, _) in getattr(self, '_records', {}).items():
                if len(re.findall(r'\[Maestr[ií]a en ', text, re.I)) >= 2:
                    catalogs.append(NodeWithScore(node=self._node_from_record(cid), score=1.0))
            catalogs.sort(key=lambda n: len(re.findall(r'\[Maestr[ií]a en ', n.node.text, re.I)), reverse=True)
        focused = [NodeWithScore(node=self._node_from_record(cid), score=1.0)
                   for cid in priority_records(question, getattr(self, '_records', {}))]
        identities = []
        branches = []
        branch_names = set()
        if re.search(r'\b(?:filiales|sedes)\b', normalize(question)) and 'unamba' in normalize(question):
            for cid, (text, _) in getattr(self, '_records', {}).items():
                names = set(map(normalize, branch_labels(text)))
                if names - branch_names:
                    branch_names |= names
                    branches.append(NodeWithScore(node=self._node_from_record(cid), score=1.0))
        roles = re.findall(r'\b(?:rector|vicerrector academico|vicerrector de investigacion)\b', normalize(question))
        if not re.match(r'^(?:quien|quienes)\b', normalize(question)):
            roles = []
        for cid, (text, _) in getattr(self, '_records', {}).items():
            # Un bloque de identificación explícita tiene prioridad sobre una mención.
            clean = normalize(text)
            if re.search(r'^#{1,6}\s+(?:dr\.?|dra\.?|mg\.?)\s+', clean, re.M) and any(
                    re.search(r'^\s*' + re.escape(role) + r'\s*$', clean, re.M) for role in roles):
                identities.append(NodeWithScore(node=self._node_from_record(cid), score=1.0))
        selected = self._pack_context(focused + catalogs[:1] + branches[:8] + identities + outlines + order(), question, limit)
        missing = []
        if selected and getattr(self, "query_planning", False):
            try:
                context = "\n\n".join(n.node.get_content(metadata_mode="llm") for n in selected)
                check = self.llm.structured_predict(CoverageCheck, COVERAGE_PROMPT, question=question, context=context)
                missing = safe_queries(question, check.missing_queries[:2])
                for query in missing:
                    search(query)
                if missing:
                    selected = self._pack_context(focused + catalogs[:1] + branches[:8] + identities + outlines + order(), question, limit)
            except Exception as exc:
                logger.warning("No se pudo revisar cobertura; se usa la evidencia recuperada: %s", exc)
        self.last_retrieval = {
            "question": question, "queries": [q for q, _ in query_hits], "broad": broad,
            "coverage_searches": missing, "candidate_count": len(by_id),
            "retrieval_seconds": round(perf_counter() - started, 3),
            "planning_seconds": round(planning_seconds, 3),
            "context_tokens": sum(len(self.tokenizer(n.node.get_content(metadata_mode="llm"))) + 16 for n in selected),
            "context_budget": self.context_token_budget,
            "sources": [{"id": n.node.node_id, "file": n.node.metadata.get("source_file"),
                         "url": n.node.metadata.get("source_url"), "section": n.node.metadata.get("header_path"),
                         "chunk_index": n.node.metadata.get("chunk_index")} for n in selected],
        }
        if LOG_RETRIEVAL:
            try:
                LOGS_DIR.mkdir(parents=True, exist_ok=True)
                with (LOGS_DIR / "retrieval.jsonl").open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(self.last_retrieval, ensure_ascii=False) + "\n")
            except OSError as exc:
                logger.warning("No se pudo registrar la recuperación: %s", exc)
        return selected

    # ---------- conversación ----------
    def reset_history(self) -> None:
        self.history.clear()

    def _standalone_question(self, question: str) -> str:
        if HISTORY_TURNS <= 0 or not self.history:
            return question
        dependent = re.search(
            r'\b(ese|esa|esos|esas|eso|dicho|dicha|anterior|mencionado|mencionada|ahi|alli)\b|^[¿\s]*y\b|^en (?:la |el )?(?:filial|sede)\b',
            normalize(question),
        )
        if not dependent:
            return question
        # Conservar literalmente la pregunta y heredar solo la pregunta previa.
        # Una respuesta del modelo nunca debe sustituir nombres ni siglas.
        previous = self.history[-1][0].split(' (Tema anterior:', 1)[0][:300]
        if re.match(r'^en (?:la |el )?(?:filial|sede)\b', normalize(question)) and re.search(
                r'\b(?:escuelas|carreras)\b', normalize(previous)):
            # El lugar nuevo sustituye al anterior; no buscar ambas filiales.
            topic = re.sub(r'\s+en\s+[^?]+$', '', previous, flags=re.I).rstrip(' ¿?!.')
            return f'{topic} {question}'
        return f'{question} (Tema anterior: {previous})'

    def _log_unanswered(self, question: str, standalone: str, reason: str) -> None:
        if not LOG_UNANSWERED:
            return
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            record = {"fecha": datetime.now().isoformat(timespec="seconds"), "pregunta": question,
                      "reformulada": standalone, "motivo": reason}
            with (LOGS_DIR / "sin_respuesta.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.warning("No pude escribir el registro de preguntas sin respuesta: %s", exc)

    def _abstain(self, question: str, standalone: str, reason: str,
                 on_token: Callable[[str], None] | None, emit_text: bool = True) -> Answer:
        text = ABSTENTION_ANSWER
        if reason == 'afirmaciones sin respaldo verificable':
            text = 'No pude verificar las afirmaciones de la respuesta con citas textuales de las fuentes recuperadas. Prueba una consulta más específica sobre la autoridad, sede o programa.'
        if reason in {'lista sin citas verificables por elemento', 'referencias inválidas del generador'}:
            text = ('Recuperé fuentes relacionadas, pero no pude validar las citas de la respuesta. '
                    'Esto no significa que la información no exista. Prueba una consulta más específica; '
                    'por ejemplo, solicita un servicio o programa concreto.')
        if emit_text and on_token is not None:
            on_token(text)
        self._log_unanswered(question, standalone, reason)
        return Answer(text, [], abstained=True, standalone_question=standalone)

    def _generate_response(self, context: str, question: str, source_count: int,
                           on_token: Callable[[str], None] | None) -> tuple[GroundedResponse, str]:
        if on_token is None:
            return self.llm.structured_predict(
                GroundedResponse, STRICT_QA_PROMPT, context_str=context, query_str=question,
            ), ""

        last = None
        emitted = ''
        valid_ids = set(range(1, source_count + 1))
        for partial in self.llm.stream_structured_predict(
            GroundedResponse, STRICT_QA_PROMPT, context_str=context, query_str=question,
        ):
            last = partial
            if not STREAM_RESPONSES or getattr(partial, 'has_evidence', None) is not True:
                continue
            ids = getattr(partial, 'source_ids', None)
            text = getattr(partial, 'answer', None)
            if not isinstance(text, str) or not isinstance(ids, list) or not ids or any(type(i) is not int or i not in valid_ids for i in ids):
                continue
            text = text.strip()
            # Una cita numérica sin cerrar todavía puede cambiar de longitud.
            text = re.sub(r'\[\d[^\]]*$', '', text).rstrip()
            text = normalize_citations(text)
            if any(int(n) not in valid_ids for n in re.findall(r'\[(\d+)\]', text)):
                continue
            if text.startswith(emitted):
                delta = text[len(emitted):]
                if delta:
                    on_token(delta)
                    emitted = text
        if last is None:
            raise RuntimeError("Ollama no devolvió una respuesta durante la transmisión.")
        response = GroundedResponse.model_validate(last.model_dump())
        return response, emitted

    def _stream_facts(self, question, context, nodes, on_token):
        last, emitted = None, ''
        for partial in self.llm.stream_structured_predict(GroundingReview, FAST_FACT_PROMPT,
                question=question, context=context):
            last = partial
            # evidence se genera antes de statement: comprobar su cita mientras
            # llega el texto y reservar el identificador hasta cerrar la frase.
            try:
                data = partial.model_dump()
                claims = data.get('claims', [])
                pieces = []
                for index, item in enumerate(claims):
                    try:
                        claim = Claim.model_validate(item)
                    except (ValueError, TypeError):
                        break
                    checked = reviewed_answer(GroundingReview(claims=[claim]), nodes, question)
                    if not checked:
                        break
                    text = checked[0]
                    if index == len(claims) - 1:
                        text = re.sub(r'\s+\[\d+\](?:,\s*\[\d+\])*$', '', text)
                    pieces.append(text)
                candidate = '\n\n'.join(pieces)
            except (ValueError, TypeError, AttributeError):
                continue
            if candidate and candidate.startswith(emitted):
                delta = candidate[len(emitted):]
                if delta:
                    on_token(delta)
                    emitted = candidate
        if last is None:
            raise RuntimeError('Ollama no devolvió afirmaciones durante la transmisión.')
        return GroundingReview.model_validate(last.model_dump()), emitted

    # ---------- API principal ----------
    def ask(self, question: str, on_token: Callable[[str], None] | None = None,
            on_status: Callable[[str], None] | None = None) -> Answer:
        """Transmite la generación y publica la validación final de fuentes."""
        question = question.strip()
        if not question:
            return self._abstain(question, question, "pregunta vacía", on_token)

        canned = small_talk_reply(question)
        if canned:
            if on_token is not None:
                on_token(canned)
            return Answer(canned, [], standalone_question=question)

        if not self.has_documents:
            text = (
                "Todavía no hay documentos cargados. Copia tus archivos .md o .txt a datos/portal/documentos/ "
                "y ejecuta iniciar.bat indexar. "
                "Después reinicia el chatbot para consultar la información."
            )
            if on_token is not None:
                on_token(text)
            return Answer(text, [], abstained=True, standalone_question=question)

        started = perf_counter()
        standalone = self._standalone_question(question)
        cache_revision = None
        cache = None
        if FAST_MODE:
            if not hasattr(self, '_response_cache'):
                self._response_cache = VerifiedAnswerCache(ANSWER_CACHE_SIZE, ANSWER_CACHE_TTL)
            cache = self._response_cache
            versions = []
            for name in ('chroma.sqlite3', 'chroma.sqlite3-wal'):
                try:
                    stat = (VECTOR_STORE_DIR / name).stat()
                    versions.append((stat.st_mtime_ns, stat.st_size))
                except OSError:
                    versions.append(None)
            cache_revision = (_fecha_actual(), LLM_MODEL, EMBED_MODEL, tuple(versions))
            cached = cache.get(standalone, cache_revision)
            if cached is not None:
                cached.standalone_question = standalone
                self.last_retrieval = {'cache_hit': True, 'total_seconds': round(perf_counter() - started, 3)}
                if on_status is not None:
                    on_status('Respuesta verificada disponible en memoria.')
                if on_token is not None:
                    on_token(cached.text)
                if HISTORY_TURNS > 0:
                    self.history.append((standalone, cached.text))
                    self.history = self.history[-HISTORY_TURNS:]
                return cached
        reformulation_seconds = perf_counter() - started
        if on_status is not None:
            on_status('Buscando fuentes...')
        nodes = self._select_context(standalone, original=question)
        if not nodes:
            return self._abstain(question, standalone, "sin evidencia recuperada", on_token)
        if on_status is not None:
            seconds = getattr(self, 'last_retrieval', {}).get('retrieval_seconds')
            elapsed = f' ({seconds:.1f} s)' if seconds is not None else ''
            on_status(f'Fuentes recuperadas{elapsed}. Generando respuesta...')

        context = "\n\n".join(item.node.get_content(metadata_mode="llm") for item in nodes)
        broad = getattr(self, 'last_retrieval', {}).get('broad', False)
        if not hasattr(self, 'last_retrieval'):
            self.last_retrieval = {}
        # Una enumeración se revisa antes de mostrarla: evita transmitir elementos
        # que el revisor deba retirar o completar después.
        generation_started = perf_counter()
        fact_checked = False
        emitted = ''

        def reject(reason):
            result = self._abstain(question, standalone, reason, on_token, emit_text=not bool(emitted))
            if emitted and on_token is not None:
                on_token('\n\n[La generación anterior no superó la validación final.]\n' + result.text)
            return result
        direct = (vocational_answer(standalone, nodes) or school_location_answer(standalone, nodes) or institutional_contact_answer(standalone, nodes) or school_fact_answer(standalone, nodes) or authority_roster_answer(standalone, nodes) or authority_identity_answer(standalone, nodes)) if FAST_MODE else None
        if broad and not direct:
            direct = (professional_catalog_answer(standalone, nodes) or faculty_career_list(standalone, nodes) or linked_service_list(standalone, nodes)
                      or linked_masters_list(standalone, nodes) or branch_reference_list(standalone, nodes))
        if direct:
            response = GroundedResponse(has_evidence=True, answer=direct,
                                        source_ids=sorted({int(n) for n in re.findall(r'\[(\d+)\]', direct)}))
            emitted = ''
        elif FAST_MODE and needs_grounding(standalone):
            if on_status is not None:
                on_status('Extrayendo afirmaciones con citas textuales...')
            proof_context = '\n\n'.join(f'FUENTE {i}:\n{evidence_text(node.node.text)}' for i, node in enumerate(nodes, 1))
            if on_token is not None and STREAM_RESPONSES:
                review, emitted = self._stream_facts(standalone, proof_context, nodes, on_token)
            else:
                review = self.llm.structured_predict(GroundingReview, FAST_FACT_PROMPT,
                    question=standalone, context=proof_context)
            checked = reviewed_answer(review, nodes, standalone)
            if not checked:
                return reject('afirmaciones sin respaldo verificable')
            response = GroundedResponse(has_evidence=True, answer=checked[0], source_ids=checked[1])
            fact_checked = True
        else:
            response, emitted = self._generate_response(context, standalone, len(nodes),
                on_token if STREAM_RESPONSES and FAST_MODE else None)
        self.last_retrieval['reformulation_seconds'] = round(reformulation_seconds, 3)
        self.last_retrieval['generation_seconds'] = round(perf_counter() - generation_started, 3)
        completed = direct or (complete_heading_list(standalone, response.answer, nodes) if broad and response.has_evidence else None)
        if completed:
            response = GroundedResponse(has_evidence=True, answer=completed,
                                        source_ids=sorted({int(n) for n in re.findall(r'\[(\d+)\]', completed)}))
        self.last_retrieval['list_validation'] = 'exact_headings' if completed else 'standard'
        review_started = perf_counter()
        if not FAST_MODE and broad and response.has_evidence and not completed and not needs_grounding(standalone) and getattr(self, 'review_lists', False):
            if on_status is not None:
                on_status('Revisando la lista y sus fuentes...')
            review_size = len(self.tokenizer(REVIEW_LIST_PROMPT.format(
                question=standalone, context=context, draft=response.answer)))
            review_size += len(self.tokenizer(json.dumps(GroundedResponse.model_json_schema()))) + 256
            if review_size + NUM_PREDICT > OLLAMA_CONTEXT_SIZE:
                raise RuntimeError('La revisión de la lista excede la ventana de contexto. Aumenta OLLAMA_CONTEXT_SIZE.')
            response = self.llm.structured_predict(
                GroundedResponse, REVIEW_LIST_PROMPT, question=standalone,
                context=context, draft=response.answer,
            )
        self.last_retrieval['review_seconds'] = round(perf_counter() - review_started, 3)
        parts = question_parts(standalone)
        if not response.has_evidence and len(parts) > 1:
            # Recupera respuestas parciales cuando el modelo rechaza la pregunta completa.
            answers, missing, source_ids = [], [], set()
            for index, part in enumerate(parts):
                query = part if index == 0 else f"Sobre el mismo tema que «{parts[0]}»: {part}"
                partial = self.llm.structured_predict(
                    GroundedResponse, STRICT_QA_PROMPT, context_str=context, query_str=query,
                )
                if partial.has_evidence:
                    answers.append(partial.answer.strip())
                    source_ids.update(partial.source_ids)
                else:
                    missing.append(part)
            if answers:
                if missing:
                    answers.append("No encontré información para: " + "; ".join(missing) + ".")
                response = GroundedResponse(
                    answer="\n\n".join(answers), source_ids=sorted(source_ids), has_evidence=True,
                )
        if not response.has_evidence:
            return reject('el modelo no encontró evidencia suficiente')
        if not direct and not fact_checked and needs_grounding(standalone):
            if on_status is not None:
                on_status('Comprobando afirmaciones con citas textuales...')
            proof_context = '\n\n'.join(f'FUENTE {i}:\n{evidence_text(node.node.text)}' for i, node in enumerate(nodes, 1))
            review = self.llm.structured_predict(GroundingReview, GROUNDING_PROMPT,
                question=standalone, draft=response.answer, context=proof_context)
            checked = reviewed_answer(review, nodes, standalone)
            if not checked:
                return reject('afirmaciones sin respaldo verificable')
            response = GroundedResponse(has_evidence=True, answer=checked[0], source_ids=checked[1])
        answer_text = normalize_citations(response.answer.strip())
        original_citations = {int(value) for value in re.findall(r'\[(\d+)\]', answer_text)}
        if broad:
            answer_text = cite_exact_headings(answer_text, nodes)
        used_sources = set(response.source_ids)
        valid_sources = set(range(1, len(nodes) + 1))
        cited_sources = {int(value) for value in re.findall(r"\[(\d+)\]", answer_text)}
        if broad:
            items = re.findall(r'^\s*(?:[-*•]|\d+[.)])\s+(.+)', answer_text, re.M)
            if any(not (ids := {int(n) for n in re.findall(r'\[(\d+)\]', item)}) or not ids <= valid_sources for item in items):
                lines = [line for line in answer_text.splitlines() if re.match(r'^\s*(?:[-*•]|\d+[.)])\s+', line)
                         and (ids := {int(n) for n in re.findall(r'\[(\d+)\]', line)}) and ids <= valid_sources]
                if not lines:
                    return reject('lista sin citas verificables por elemento')
                answer_text = 'Encontré estos elementos con referencias (lista parcial):\n\n' + '\n'.join(lines) + '\n\nNo pude verificar los demás elementos de la respuesta.'
                cited_sources = {int(n) for n in re.findall(r'\[(\d+)\]', answer_text)}
                used_sources = set(cited_sources)
            # Se incluyen las referencias añadidas por coincidencia literal.
            used_sources |= cited_sources - original_citations
        if not answer_text or not used_sources or not used_sources <= valid_sources or not cited_sources <= valid_sources:
            return reject('referencias inválidas del generador')
        used_sources |= cited_sources
        if not cited_sources:
            answer_text += "\n\nFuentes: " + ", ".join(f"[{i}]" for i in sorted(used_sources)) + "."
        else:
            used_sources = cited_sources
        if on_token is not None:
            if not answer_text.startswith(emitted):
                delta = '\n\nRespuesta corregida tras verificar las fuentes:\n' + answer_text
            else:
                delta = answer_text[len(emitted):]
            if delta:
                on_token(delta)

        if HISTORY_TURNS > 0:
            self.history.append((standalone, answer_text))
            self.history = self.history[-HISTORY_TURNS:]

        sources: list[Source] = []
        for number, item in enumerate(nodes, start=1):
            if number not in used_sources:
                continue
            metadata: dict[str, Any] = item.node.metadata or {}
            excerpt = item.node.get_content().strip()
            sources.append(
                Source(
                    file=str(metadata.get("source_file") or metadata.get("file_name") or "documento"),
                    score=item.score,
                    excerpt=excerpt[:700] + ("…" if len(excerpt) > 700 else ""),
                    title=str(metadata.get("titulo") or ""),
                    number=number,
                    url=str(metadata.get("source_url") or ""),
                )
            )
        self.last_retrieval['total_seconds'] = round(perf_counter() - started, 3)
        if LOG_RETRIEVAL:
            try:
                LOGS_DIR.mkdir(parents=True, exist_ok=True)
                timing = {key: value for key, value in self.last_retrieval.items()
                          if key.endswith('_seconds')}
                timing['question'] = standalone
                with (LOGS_DIR / 'latency.jsonl').open('a', encoding='utf-8') as handle:
                    handle.write(json.dumps(timing, ensure_ascii=False) + '\n')
            except OSError as exc:
                logger.warning('No se pudo registrar la latencia: %s', exc)
        result = Answer(answer_text, sources, standalone_question=standalone)
        if cache is not None:
            cache.put(standalone, cache_revision, result)
        return result
