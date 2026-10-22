"""PDF -> PaddleOCR (Ollama) -> TXT -> Qwen3 embeddings -> Chroma persistente."""
import argparse
import hashlib
import html
import io
import json
import re
import shutil
import sys
import time
import math
from pathlib import Path

import chromadb
import httpx
import ollama
from pdf2image import convert_from_path
from pypdf import PdfReader, PdfWriter
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from motores.pdf.motor_contexto import titulo_documental
from configuracion import servidor as config

BASE = Path(__file__).resolve().parent
OCR_MODEL = config.OCR_MODEL
EMBEDDING_MODEL = config.EMBEDDING_MODEL
COLLECTION_NAME = config.COLECCION_DOCS
POPPLER = config.PDF_POPPLER_DIR


def limpiar(texto):
    texto = re.sub(r"<\|[^>]+\|>", "", texto)
    texto = re.sub(r"</t[dh]\s*>", " | ", texto, flags=re.I)
    texto = re.sub(r"</tr\s*>|<br\s*/?>", "\n", texto, flags=re.I)
    texto = re.sub(r"</?(?:table|tbody|thead|tr|td|th|div|p)\b[^>]*>", "", texto, flags=re.I)
    texto = html.unescape(texto).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", texto).strip()


def chunks(texto, size=2000, overlap=300):
    if not 0 <= overlap < size:
        raise ValueError("Overlap debe ser menor que el tamaño de chunk.")
    start = 0
    while start < len(texto):
        end = min(start + size, len(texto))
        if end < len(texto):
            cut = texto.rfind(" ", start + size // 2, end)
            if cut > start:
                end = cut
        if texto[start:end].strip():
            yield texto[start:end].strip()
        if end == len(texto):
            break
        start = max(start + 1, end - overlap)


class OCRIncompleto(RuntimeError):
    """Una respuesta vacía o truncada no debe guardarse como OCR correcto."""


def recuperable(exc):
    return (isinstance(exc, (httpx.TransportError, ConnectionError, OCRIncompleto))
            or isinstance(exc, ollama.ResponseError)
            and (exc.status_code == 429 or exc.status_code >= 500))


def detalle_error(exc):
    return f"{type(exc).__name__}: {exc}"


def ocr(client, image, args):
    buf = io.BytesIO()
    with image.convert("RGB") as rgb:
        rgb.thumbnail((args.max_lado, args.max_lado))
        rgb.save(buf, format="PNG")
    inicio = time.monotonic()
    ultimo_aviso = inicio
    response = client.generate(
        model=OCR_MODEL, prompt="OCR:", images=[buf.getvalue()],
        stream=True, keep_alive="5m",
        options={"temperature": 0, "num_predict": 8192, "num_ctx": 16384},
    )
    fragments = []
    completo = False
    try:
        for event in response:
            ahora = time.monotonic()
            if ahora - inicio > args.timeout_ocr:
                raise OCRIncompleto("OCR superó el tiempo total permitido.")
            if ahora - ultimo_aviso >= 30:
                print(f"    OCR generando... {int(ahora - inicio)} s", flush=True)
                ultimo_aviso = ahora
            fragments.append(event.response)
            if event.done:
                if event.done_reason == "length":
                    raise OCRIncompleto("OCR truncado; se intentará con secciones más pequeñas.")
                completo = True
    finally:
        response.close()
    if not completo:
        raise OCRIncompleto("Ollama cerró la respuesta antes de completarla.")
    text = limpiar("".join(fragments))
    if not text:
        raise OCRIncompleto("OCR sin texto en una imagen con contenido visible.")
    return text


def es_blanca(image):
    # Conservador: solo omitir una superficie casi blanca y uniforme.
    # Una respuesta OCR vacía, por sí sola, nunca prueba que la página esté en blanco.
    with image.convert("L") as gray:
        minimo, maximo = gray.getextrema()
    return minimo >= 245 and maximo - minimo <= 3


def unir_secciones(primera, segunda):
    """Quita solo líneas idénticas en la zona solapada entre dos secciones."""
    a, b = primera.splitlines(), segunda.splitlines()
    for n in range(min(12, len(a), len(b)), 0, -1):
        if [s.strip() for s in a[-n:]] == [s.strip() for s in b[:n]]:
            b = b[n:]
            break
    return "\n".join(a + b).strip()


def ocr_secciones(client, image, args, depth=0):
    if es_blanca(image):
        return ""
    try:
        return ocr(client, image, args)
    except Exception as exc:
        if not recuperable(exc) or depth >= args.nivel_secciones:
            raise
        print(f"    Sección difícil ({detalle_error(exc)}); dividiendo en mitades.", flush=True)
        time.sleep(args.pausa_reintento)
    width, height = image.size
    if height < 128:
        raise OCRIncompleto("Sección demasiado pequeña para seguir dividiendo.")
    medio = height // 2
    overlap = min(24, height // 20)
    textos = []
    for box in ((0, 0, width, medio + overlap), (0, medio - overlap, width, height)):
        with image.crop(box) as region:
            textos.append(ocr_secciones(client, region, args, depth + 1))
    return unir_secciones(*textos)


def leer_pagina(ruta, numero, client, poppler, args):
    """Reintentos completos y último intento por mitades de la imagen."""
    ultimo_error = None
    for intento in range(args.intentos_ocr + 1):
        por_secciones = intento == args.intentos_ocr
        dpi = args.dpi if por_secciones else max(100, args.dpi - intento * 40)
        images = []
        etapa = "renderizado"
        print(f"    {'Secciones' if por_secciones else 'Intento ' + str(intento + 1)}: {dpi} DPI", flush=True)
        try:
            images = convert_from_path(str(ruta), dpi=dpi, first_page=numero,
                                       last_page=numero, poppler_path=poppler,
                                       thread_count=1, timeout=args.timeout_render,
                                       size=max(800, int(args.max_lado * dpi / args.dpi)
                                                * (2 if por_secciones else 1)))
            if len(images) != 1:
                raise OCRIncompleto("El renderizado no devolvió exactamente una página.")
            image = images[0]
            if es_blanca(image):
                return "", "blanca"
            etapa = "OCR"
            text = (ocr_secciones(client, image, args) if por_secciones
                    else ocr(client, image, args))
            if not text.strip():
                raise OCRIncompleto("No se obtuvo texto completo de las secciones.")
            return text, "secciones" if por_secciones else "ocr"
        except Exception as exc:
            ultimo_error = RuntimeError(f"{etapa}, intento {intento + 1}: {detalle_error(exc)}")
            print(f"    {ultimo_error}", flush=True)
            if etapa == "OCR" and not recuperable(exc):
                raise ultimo_error from exc
        finally:
            for image in images:
                image.close()
        if not por_secciones:
            time.sleep(args.pausa_reintento)
    raise ultimo_error


def embeddings_con_reintentos(client, documentos, args):
    for intento in range(args.intentos_ocr):
        try:
            result = client.embed(model=EMBEDDING_MODEL, input=documentos,
                                  truncate=False, keep_alive="5m")
            vectors = result.embeddings
            if (len(vectors) != len(documentos)
                    or any(len(v) != 1024 or not all(math.isfinite(x) for x in v) for v in vectors)):
                raise OCRIncompleto("Embeddings incompletos o dimensiones inesperadas.")
            return vectors
        except Exception as exc:
            if not recuperable(exc):
                raise
            print(f"    Embeddings, intento {intento + 1}: {detalle_error(exc)}", flush=True)
            if intento + 1 < args.intentos_ocr:
                time.sleep(args.pausa_reintento)
            elif len(documentos) == 1:
                raise
    medio = len(documentos) // 2
    print(f"    Reduciendo lote de embeddings: {len(documentos)} -> {medio}.", flush=True)
    return (embeddings_con_reintentos(client, documentos[:medio], args)
            + embeddings_con_reintentos(client, documentos[medio:], args))


def atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def hash_archivo(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def dividir_pdf(path, destino, max_paginas=40, max_mb=30):
    """Divide en mitades sucesivas; devuelve rutas y páginas originales (1-based).

    Una sola página no se puede dividir, aunque exceda el límite de MB.
    Las partes permanecen en disco para inspección y el original no se modifica.
    """
    if max_paginas < 1 or max_mb <= 0:
        raise ValueError("Los límites de páginas y MB deben ser positivos.")
    destino.mkdir(parents=True, exist_ok=True)
    partes = []
    with path.open("rb") as stream:
        reader = PdfReader(stream)
        total = len(reader.pages)
        if not total:
            raise RuntimeError("PDF sin páginas.")

        def dividir(inicio, fin):
            cantidad = fin - inicio
            if cantidad > max_paginas:
                medio = inicio + cantidad // 2
                dividir(inicio, medio)
                dividir(medio, fin)
                return
            target = destino / f"paginas_{inicio + 1:05d}_{fin:05d}.pdf"
            writer = PdfWriter()
            try:
                for i in range(inicio, fin):
                    writer.add_page(reader.pages[i])
                with target.open("wb") as output:
                    writer.write(output)
            finally:
                writer.close()
            if target.stat().st_size > max_mb * 1024 * 1024 and cantidad > 1:
                target.unlink()
                medio = inicio + cantidad // 2
                dividir(inicio, medio)
                dividir(medio, fin)
            else:
                if target.stat().st_size > max_mb * 1024 * 1024:
                    print(f"  Aviso: página {inicio + 1} supera el límite de MB.", flush=True)
                partes.append({"ruta": str(target.resolve()), "pagina_inicio": inicio + 1,
                               "pagina_fin": fin})

        dividir(0, total)
    return partes, total


def preparar_paginas(partes, cache, client, poppler, args, manifest, txt_path):
    """Guarda cada éxito inmediatamente y prueba TODAS las páginas antes de fallar."""
    estados = {}
    pendientes = []
    reporte = cache / "estado_paginas.json"
    compartido = (args.output / "ocr_compartido" /
                  hashlib.sha256(OCR_MODEL.encode()).hexdigest()[:16] / manifest["sha256"])
    blancas_confirmadas = set()
    for spec in args.paginas_blancas:
        nombre, numeros = spec.rsplit(":", 1)
        if nombre.casefold() == Path(manifest["source"]).name.casefold():
            blancas_confirmadas.update(int(n.strip()) for n in numeros.split(","))
    if any(n < 1 or n > manifest["total_pages"] for n in blancas_confirmadas):
        raise ValueError("Una página marcada como blanca está fuera del PDF.")
    partial = txt_path.with_suffix(".parcial.txt")
    with partial.open("w", encoding="utf-8") as completo:
        for parte in partes:
            textos = []
            for page in range(parte["pagina_inicio"], parte["pagina_fin"] + 1):
                cached = cache / f"{page:05d}.txt"
                status_path = cache / f"{page:05d}.json"
                print(f"  OCR | Parte {parte['parte']}/{len(partes)} | "
                      f"Página {page}/{manifest['total_pages']}", flush=True)
                try:
                    text = None
                    metodo = "cache"
                    if page in blancas_confirmadas:
                        text, metodo = "", "blanca_confirmada"
                    elif not args.rehacer_ocr:
                        for candidata in (cached, compartido / cached.name):
                            if not candidata.exists():
                                continue
                            candidate = candidata.read_text(encoding="utf-8")
                            status = {}
                            if candidata.with_suffix(".json").exists():
                                try:
                                    status = json.loads(candidata.with_suffix(".json").read_text(encoding="utf-8"))
                                except (ValueError, OSError):
                                    pass
                            if candidate.strip():
                                text = candidate
                                metodo = "cache" if candidata == cached else "cache_compartida"
                                break
                            if status.get("metodo") in ("blanca", "blanca_confirmada"):
                                text, metodo = "", status["metodo"]
                                break
                    if text is None:
                        text, metodo = leer_pagina(parte["ruta"], page - parte["pagina_inicio"] + 1,
                                                  client, poppler, args)
                    atomic_text(cached, text)
                    atomic_text(status_path, json.dumps({"metodo": metodo}, ensure_ascii=False))
                    atomic_text(compartido / cached.name, text)
                    atomic_text((compartido / cached.name).with_suffix(".json"),
                                json.dumps({"metodo": metodo}, ensure_ascii=False))
                    print(f"    Guardada ({metodo}).", flush=True)
                    estados[str(page)] = {"estado": "ok", "metodo": metodo}
                    contenido = text if text.strip() else "[PÁGINA EN BLANCO]"
                except Exception as exc:
                    pendientes.append(page)
                    estados[str(page)] = {"estado": "pendiente", "detalle": detalle_error(exc)}
                    contenido = f"[OCR PENDIENTE: {detalle_error(exc)}]"
                    print(f"    Página {page} pendiente; continúo con la siguiente.", flush=True)
                pagina_txt = f"=== PÁGINA {page} ===\n{contenido}"
                completo.write(("\n\n" if page > 1 else "") + pagina_txt)
                completo.flush()
                textos.append(pagina_txt)
                atomic_text(reporte, json.dumps({"source": manifest["source"],
                                                "sha256": manifest["sha256"],
                                                "total_pages": manifest["total_pages"],
                                                "paginas": estados,
                                                "pendientes": pendientes}, ensure_ascii=False, indent=2))
            atomic_text(Path(parte["ruta"]).with_suffix(".txt"), "\n\n".join(textos))
    if pendientes:
        raise RuntimeError(f"Páginas pendientes: {pendientes}. Se conservó el avance. "
                           f"Vuelva a ejecutar para reintentarlas. Reporte: {reporte}. "
                           "La colección no se modificó para este PDF.")
    return partial


def vectores_cache(client, documentos, carpeta, args):
    rutas = [carpeta / (hashlib.sha256((EMBEDDING_MODEL + "\n" + doc).encode()).hexdigest() + ".json")
             for doc in documentos]
    vectores = [None] * len(documentos)
    faltan = []
    for i, ruta in enumerate(rutas):
        try:
            vector = json.loads(ruta.read_text(encoding="utf-8"))
            if len(vector) == 1024 and all(math.isfinite(x) for x in vector):
                vectores[i] = vector
            else:
                faltan.append(i)
        except (OSError, ValueError, TypeError):
            faltan.append(i)
    if faltan:
        nuevos = embeddings_con_reintentos(client, [documentos[i] for i in faltan], args)
        for i, vector in zip(faltan, nuevos):
            atomic_text(rutas[i], json.dumps(vector))
            vectores[i] = vector
    return vectores


def procesar(path, args, client, collection, poppler):
    relative = path.relative_to(args.docs)
    source = relative.as_posix()
    source_id = hashlib.sha256(source.casefold().encode()).hexdigest()
    fingerprint = hash_archivo(path)
    cache = args.output / "paginas" / source_id / fingerprint
    carpeta_partes = args.output / "partes_pdf" / source_id / fingerprint
    partes, page_count = dividir_pdf(path, carpeta_partes, args.max_paginas_parte, args.max_mb_parte)
    for index, parte in enumerate(partes, 1):
        parte["parte"] = index
    manifest = {"source": source, "source_id": source_id, "sha256": fingerprint,
                "total_pages": page_count, "total_parts": len(partes), "partes": partes}
    atomic_text(carpeta_partes / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"  {page_count} páginas, {len(partes)} partes.", flush=True)
    txt_path = args.output / relative.with_suffix(".txt")
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    # Completar todo el OCR antes de cargar embeddings: evita intercambiar modelos
    # en la GPU cada ocho chunks y conserva la caché de la versión anterior.
    partial = preparar_paginas(partes, cache, client, poppler, args, manifest, txt_path)
    print("  OCR completo. Preparando embeddings por lotes...", flush=True)
    try:
        client.generate(model=OCR_MODEL, keep_alive=0)
    except Exception as exc:
        print(f"  Aviso al liberar modelo OCR: {detalle_error(exc)}", flush=True)
    total_chunks = 0
    titulo = None
    # Preparar en disco antes de modificar Chroma; no acumular el PDF completo en RAM.
    with tempfile.TemporaryDirectory(prefix="indexacion_", dir=str(carpeta_partes)) as staging:
        stage = Path(staging)
        batch_count = 0
        batch = []

        def guardar_lote():
            nonlocal batch_count
            if not batch:
                return
            vectors = vectores_cache(client, [r["document"] for r in batch],
                                    args.output / "embeddings_cache", args)
            for record, vector in zip(batch, vectors):
                record["embedding"] = vector
            atomic_text(stage / f"{batch_count:08d}.json", json.dumps(batch, ensure_ascii=False))
            batch_count += 1
            batch.clear()
            print(f"  Embeddings: {total_chunks} chunks preparados.", flush=True)

        with (stage / "completo.txt").open("w", encoding="utf-8") as completo:
            for parte in partes:
                textos_parte = []
                for page in range(parte["pagina_inicio"], parte["pagina_fin"] + 1):
                    cached = cache / f"{page:05d}.txt"
                    text = cached.read_text(encoding="utf-8")
                    if titulo is None and text.strip():
                        titulo = titulo_documental(text)
                    texto_pagina = f"=== PÁGINA {page} ===\n" + (text or "[PÁGINA EN BLANCO]")
                    completo.write(("\n\n" if page > 1 else "") + texto_pagina)
                    textos_parte.append(texto_pagina)
                    for chunk, body in enumerate(chunks(text), 1):
                        total_chunks += 1
                        batch.append({"id": f"{source_id}_p{page}_c{chunk}",
                                      "document": f"DOCUMENTO: {relative}\nPÁGINA: {page}\n\n{body}",
                                      "metadata": {"source": source, "source_id": source_id,
                                                   "file_name": path.name, "page": page, "chunk": chunk,
                                                   "total_pages": page_count, "sha256": fingerprint,
                                                   "part": parte["parte"], "total_parts": len(partes),
                                                   "part_page_start": parte["pagina_inicio"],
                                                   "part_page_end": parte["pagina_fin"],
                                                   "ocr_model": OCR_MODEL, "document_title": titulo,
                                                   "embedding_model": EMBEDDING_MODEL}})
                        if len(batch) >= args.lote_embeddings:
                            guardar_lote()
                atomic_text(Path(parte["ruta"]).with_suffix(".txt"), "\n\n".join(textos_parte))
                guardar_lote()
                print(f"  Parte preparada: {total_chunks} chunks acumulados.", flush=True)
        old_ids = collection.get(where={"source": source}, include=[])["ids"]
        expected = set()
        for index in range(batch_count):
            records = json.loads((stage / f"{index:08d}.json").read_text(encoding="utf-8"))
            for record in records:
                record["metadata"]["total_chunks"] = total_chunks
                expected.add(record["id"])
            collection.upsert(ids=[r["id"] for r in records],
                              documents=[r["document"] for r in records],
                              metadatas=[r["metadata"] for r in records],
                              embeddings=[r["embedding"] for r in records])
        obsolete = sorted(set(old_ids) - expected)
        for start in range(0, len(obsolete), 128):
            collection.delete(ids=obsolete[start:start + 128])
        saved = collection.get(where={"source": source}, include=[])["ids"]
        if set(saved) != expected:
            raise RuntimeError("La verificación de los registros guardados falló.")
        (stage / "completo.txt").replace(txt_path)
        partial.unlink(missing_ok=True)
    print(f"  OK: {total_chunks} chunks. TXT: {txt_path}", flush=True)
    return total_chunks


def procesar_texto(path, args, client, collection):
    """Indexa texto completo disponible sin volver a hacer OCR ni usar Internet."""
    relative = path.relative_to(args.docs)
    txt = args.output / relative.with_suffix('.txt')
    reader = PdfReader(str(path))
    total_pages = len(reader.pages)
    if not txt.exists():
        # Un PDF digital puede leerse localmente sin el modelo OCR opcional.
        pages = [(page.extract_text() or '').strip() for page in reader.pages]
        if not all(pages):
            raise ValueError('Falta texto completo. Conservado el OCR parcial; este PDF necesita completar OCR.')
        text = '\n\n'.join(f'=== PÁGINA {i} ===\n{body}' for i, body in enumerate(pages, 1))
    else:
        text = txt.read_text(encoding='utf-8-sig')
    sections = re.split(r'^=== PÁGINA (\d+) ===\s*\n', text, flags=re.M)
    numbers = [int(sections[i]) for i in range(1, len(sections), 2)]
    if numbers != list(range(1, total_pages + 1)):
        raise ValueError('Texto incompleto o páginas fuera de orden; no se indexa como documento completo.')
    source = relative.as_posix()
    source_id = hashlib.sha256(source.casefold().encode()).hexdigest()
    fingerprint = hash_archivo(path)
    records = []
    title = ''
    for i in range(1, len(sections), 2):
        page = int(sections[i]); body = sections[i + 1].strip()
        if not body:
            raise ValueError(f'Página {page} sin texto ni confirmación de página en blanco.')
        if not title:
            title = titulo_documental(body)
        for chunk, part in enumerate(chunks(body), 1):
            records.append({'id': f'{source_id}_p{page}_c{chunk}',
                'document': f'DOCUMENTO: {relative}\nPÁGINA: {page}\n\n{part}',
                'metadata': {'source': source, 'source_id': source_id, 'file_name': path.name,
                    'page': page, 'chunk': chunk, 'total_pages': total_pages, 'sha256': fingerprint,
                    'document_title': title, 'embedding_model': EMBEDDING_MODEL}})
    if not records:
        raise ValueError('El documento no contiene texto indexable.')
    # Preparar todos los vectores antes de modificar el documento en Chroma.
    vectors = []
    for start in range(0, len(records), args.lote_embeddings):
        batch = records[start:start + args.lote_embeddings]
        vectors.extend(vectores_cache(client, [row['document'] for row in batch], args.output / 'embeddings_cache', args))
    for row in records:
        row['metadata']['total_chunks'] = len(records)
    old_ids = set(collection.get(where={'source': source}, include=[])['ids'])
    for start in range(0, len(records), 100):
        batch = records[start:start + 100]
        collection.upsert(ids=[row['id'] for row in batch], documents=[row['document'] for row in batch],
                          metadatas=[row['metadata'] for row in batch], embeddings=vectors[start:start + 100])
    obsolete = list(old_ids - {row['id'] for row in records})
    if obsolete:
        collection.delete(ids=obsolete)
    if not txt.exists():
        atomic_text(txt, text)
    print(f'  Texto completo indexado: {total_pages} páginas, {len(records)} fragmentos.', flush=True)
    return len(records)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--solo-texto", action="store_true", help="Indexar texto completo existente sin usar el modelo OCR.")
    parser.add_argument("--docs", type=Path, default=config.PDF_ORIGINALS_DIR)
    parser.add_argument("--db", type=Path, default=config.CHROMA_DIR)
    parser.add_argument("--output", type=Path, default=config.PDF_TEXT_DIR)
    parser.add_argument("--collection", default=COLLECTION_NAME)
    parser.add_argument("--host", default=config.OLLAMA_HOST)
    parser.add_argument("--poppler", type=Path)
    parser.add_argument("--rehacer-ocr", action="store_true")
    parser.add_argument("--max-paginas-parte", type=int, default=40,
                        help="Máximo de páginas por parte (default: 40).")
    parser.add_argument("--max-mb-parte", type=float, default=30,
                        help="Máximo de MB por parte salvo páginas indivisibles (default: 30).")
    parser.add_argument("--timeout-ocr", type=float, default=1200,
                        help="Segundos por intento OCR y espera de Ollama (default: 1200).")
    parser.add_argument("--timeout-render", type=int, default=300)
    parser.add_argument("--intentos-ocr", type=int, default=2,
                        help="Intentos completos antes de leer por secciones (default: 2).")
    parser.add_argument("--nivel-secciones", type=int, default=2,
                        help="Máxima profundidad de división de imagen: 1 a 4 (default: 2).")
    parser.add_argument("--pausa-reintento", type=float, default=5)
    parser.add_argument("--dpi", type=int, default=200)
    parser.add_argument("--max-lado", type=int, default=2400,
                        help="Lado máximo en píxeles por imagen enviada al OCR (default: 2400).")
    parser.add_argument("--lote-embeddings", type=int, default=4)
    parser.add_argument("--paginas-blancas", action="append", default=[], metavar="ARCHIVO.pdf:12,13",
                        help="Omitir páginas que usted ya verificó como blancas. Se puede repetir.")
    args = parser.parse_args()
    for spec in args.paginas_blancas:
        try:
            nombre, numeros = spec.rsplit(":", 1)
            if not nombre or any(int(n.strip()) < 1 for n in numeros.split(",")):
                raise ValueError()
        except ValueError:
            parser.error("Use --paginas-blancas 'NOMBRE.pdf:12,13' con páginas positivas.")
    if (args.max_paginas_parte < 1 or not math.isfinite(args.max_mb_parte) or args.max_mb_parte <= 0
            or not math.isfinite(args.timeout_ocr) or args.timeout_ocr <= 0 or args.timeout_render <= 0
            or args.intentos_ocr < 1 or not 1 <= args.nivel_secciones <= 4
            or not math.isfinite(args.pausa_reintento) or args.pausa_reintento < 0
            or args.dpi < 100 or args.max_lado < 800 or args.lote_embeddings < 1):
        parser.error("Límites inválidos: valores positivos, DPI >= 100, lado >= 800, "
                     "niveles entre 1 y 4 y pausa >= 0.")
    args.docs = args.docs.resolve()
    files = sorted(p for p in args.docs.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")
    if not files:
        print(f"No hay PDF en {args.docs}. Copie allí sus documentos y vuelva a ejecutar.")
        return 1
    poppler = args.poppler or (POPPLER if (POPPLER / "pdftoppm.exe").exists() else None)
    if not args.solo_texto and poppler is None and not shutil.which("pdftoppm"):
        raise RuntimeError("No se encontró Poppler. Indique --poppler RUTA_A_BIN.")
    client = ollama.Client(host=args.host,
                           timeout=httpx.Timeout(args.timeout_ocr, connect=30), trust_env=False)
    available = {m.model for m in client.list().models}
    for model in ((EMBEDDING_MODEL,) if args.solo_texto else (OCR_MODEL, EMBEDDING_MODEL)):
        if model not in available:
            raise RuntimeError(f"Falta el modelo. Ejecute: ollama pull {model}")
    chroma = chromadb.PersistentClient(path=str(args.db), settings=chromadb.Settings(anonymized_telemetry=False))
    collection = chroma.get_or_create_collection(
        name=args.collection, embedding_function=None,
        metadata={"embedding_model": EMBEDDING_MODEL, "ocr_model": OCR_MODEL,
                  "chunk_size": 2000, "chunk_overlap": 300, "hnsw:space": "cosine"})
    if (collection.metadata or {}).get("embedding_model") != EMBEDDING_MODEL:
        raise RuntimeError("La colección utiliza otro modelo. Elija una colección nueva con --collection.")
    print(f"PDF: {len(files)} | Chroma: {args.db} | Colección: {args.collection}", flush=True)
    results = []
    for path in files:
        print(f"\nProcesando: {path.name}", flush=True)
        try:
            count = (procesar_texto(path, args, client, collection) if args.solo_texto else
                     procesar(path, args, client, collection, str(poppler) if poppler else None))
            results.append({"archivo": str(path), "estado": "ok", "chunks": count})
        except Exception as exc:
            print(f"  ERROR: {exc}", flush=True)
            results.append({"archivo": str(path), "estado": "error", "detalle": str(exc)})
        atomic_text(args.output / "resultado.json", json.dumps(results, ensure_ascii=False, indent=2))
    errors = sum(r["estado"] == "error" for r in results)
    print(f"\nCorrectos: {len(results) - errors} | Errores: {errors} | Registros en colección: {collection.count()}")
    return 1 if errors else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
