"""Extrae e indexa páginas públicas de la UNAMBA con Crawl4AI, Ollama y ChromaDB."""
import argparse
import asyncio
import hashlib
import json
import re
import sys
import unicodedata
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse, urlunparse, parse_qsl, urlencode

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "servidor"))
sys.path.insert(0, str(ROOT / "motores"))

import chromadb
import ollama
from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

import config
from web_extraction import VERSION, ERROR_PAGE, extract_html, structured_chunks


URL_BLOQUEADA = re.compile(
    r"(logout|admin|wp-admin|wp-login|intranet|campus|aula|mail|correo|webmail|"
    r"usuario|user|account|cuenta|password|passwd|clave|session|sesion|"
    r"portal_alumno|appunamba|wp-json|xmlrpc)",
    re.I,
)
RUTA_NO_CONTENIDO = re.compile(
    r"(^|/)(feed|comments/feed|category|tag|author|archives?)(/|$)",
    re.I,
)
EXTENSIONES_NO_WEB = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp",
    ".csv", ".txt", ".rtf", ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".css", ".js",
    ".mp4", ".mp3", ".avi", ".mov", ".wmv", ".mkv", ".webm", ".wav",
)
EXTENSIONES_COMPLEMENTARIAS = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp",
    ".csv", ".txt", ".rtf", ".zip", ".rar", ".7z", ".mp4", ".mp3", ".avi", ".mov",
    ".wmv", ".mkv", ".webm", ".wav",
)
DOMINIOS_COMPLEMENTARIOS = (
    "youtube.com", "youtu.be", "vimeo.com", "drive.google.com", "docs.google.com",
    "forms.gle", "forms.office.com",
)
LINEA_NAVEGACION = re.compile(
    r"^(inicio|home|menu|menú|buscar|search|copyright|todos los derechos|"
    r"saltar al contenido|facebook|twitter|instagram|youtube|linkedin|"
    r"leer más|read more|ver más|publicado por|share this post|compartir|"
    r"toda las categorías|todas las categorías|all categories|recent posts|"
    r"publicaciones recientes|meta|acceder|feed de entradas|feed de comentarios|"
    r"wordpress\.org|contador de visitas|previous next|cerrar|cargando|tienes dudas|"
    r"presione aquí en el robot|presione aqui en el robot)\b",
    re.I,
)
PREFIJO_ESTRUCTURA = re.compile(r"^(?:Título principal|Sección|Subsección):\s*", re.I)
LINEA_RUIDO = re.compile(
    r"(pusulabet|jojobet|kralbet|casibom|betasus|dinamobet|mobilbahis|"
    r"holiganbet|betyap|zbahis|marsbahis|crypto gambling|buy weed|"
    r"deneme bonusu|wp/modins|api\.whatsapp\.com)",
    re.I,
)
LINEA_FORMULARIO = re.compile(
    r"^(¿?primera vez ingresando|¿?olvidaste tu contraseña|recuperar acceso|"
    r"regresar al login|inicia sesión|ingrese su número de dni|usuario|contraseña)\b",
    re.I,
)
LINEA_BREADCRUMB = re.compile(r"^\d+\.\s*(home|inicio|noticias|sin categoría|institucional)\b", re.I)
LINEA_MENU_INSTITUCIONAL = re.compile(
    r"^(la universidad|nuestra universidad|bienvenida del rector|misión, visión y valores|"
    r"mision, vision y valores|licenciamiento institucional|organigrama institucional|"
    r"autoridades|facultades|escuelas profesionales|dependencias|servicios digitales|"
    r"noticias y eventos|transparencia|convocatorias|comité electoral universitario|"
    r"comite electoral universitario|oportunidades laborales|directorio telefónico|"
    r"directorio telefonico|libro de reclamaciones|portal de transparencia estándar|"
    r"portal de transparencia estandar|más información aquí|mas informacion aqui)\s*:",
    re.I,
)
URL_EN_TEXTO = re.compile(r"https?://[^\s<>)\]]+")
COMENTARIO_HTML = re.compile(r"<!--.*?-->", re.S)
ADVERTENCIA_SISTEMA = re.compile(
    r"(tu navegador no admite la reproducción de video html\s*5|"
    r"tu navegador no soporta video html\s*5|"
    r"your browser does not support html\s*5 video)",
    re.I,
)
EMOJI = re.compile(
    "["
    "\U0001F1E6-\U0001F1FF"
    "\U0001F300-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U00002600-\U000026FF"
    "]+",
    re.UNICODE,
)


def dominio(url):
    host = urlparse(url).netloc.lower().split(":")[0]
    if host.startswith("www."):
        return host[4:]
    return host


def permitido(url, dominios):
    host = dominio(url)
    return host in dominios


def normalizar_url(url):
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        return ""
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    host = parsed.netloc.lower()
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
                             if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid", "returnurl"}))
    return urlunparse((parsed.scheme.lower(), host, path, "", query, ""))


def quitar_descarga_pegada(url):
    extensiones = "|".join(re.escape(ext.lstrip(".")) for ext in EXTENSIONES_COMPLEMENTARIAS)
    return re.sub(rf"(?i)(\.({extensiones}))(?:descarga|descargar)$", r"\1", url.strip())


def url_para_texto(url):
    """Normaliza enlaces visibles sin quitar archivos complementarios."""
    parsed = urlparse(quitar_descarga_pegada((url or "").strip().rstrip(".,;:")))
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    host = parsed.netloc.lower()
    path = parsed.path or "/"
    return urlunparse((parsed.scheme.lower(), host, path, "", parsed.query, ""))


def url_visible_util(url):
    parsed = urlparse(url or "")
    if not parsed.scheme or not parsed.netloc:
        return False
    if URL_BLOQUEADA.search(url) or LINEA_RUIDO.search(url):
        return False
    return not RUTA_NO_CONTENIDO.search((parsed.path or "/").strip("/"))


def es_enlace_complementario(url):
    parsed = urlparse(url or "")
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    return path.endswith(EXTENSIONES_COMPLEMENTARIAS) or any(
        host == domain or host.endswith("." + domain)
        for domain in DOMINIOS_COMPLEMENTARIOS
    )


def etiqueta_enlace_complementario(url):
    if not url or not url_visible_util(url) or not es_enlace_complementario(url):
        return ""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if any(host == domain or host.endswith("." + domain) for domain in ("youtube.com", "youtu.be", "vimeo.com")):
        return f"Video complementario: {url}"
    if any(host == domain or host.endswith("." + domain) for domain in ("forms.gle", "forms.office.com")):
        return f"Formulario complementario: {url}"
    if any(host == domain or host.endswith("." + domain) for domain in ("drive.google.com", "docs.google.com")):
        return f"Documento complementario: {url}"
    nombre = unquote(Path(parsed.path).name).strip()
    if nombre:
        nombre = re.sub(r"[_-]+", " ", nombre)
        nombre = re.sub(r"\s+", " ", nombre)
        return f"Archivo complementario {nombre}: {url}"
    return f"Enlace complementario: {url}"


def es_url_publica(url):
    path = urlparse(url).path.lower()
    if re.search(r"/(administrador|docente|estudiante)(/login)?/?$", path):
        return False
    if re.search(r"(?:^|/)(?:wp-admin|wp-login\.php|admin|logout|intranet|webmail|password|wp-json|xmlrpc\.php|portal_alumno(?:\.html)?|app_docente(?:\.html)?|account)(?:/|$)", urlparse(url).path, re.I):
        return False
    path = urlparse(url).path.lower()
    if RUTA_NO_CONTENIDO.search(path.strip("/")):
        return False
    return not path.endswith(EXTENSIONES_NO_WEB)


def texto_markdown(resultado):
    markdown = getattr(resultado, "markdown", None)
    if markdown and hasattr(markdown, "raw_markdown"):
        return markdown.raw_markdown or ""
    if markdown and hasattr(markdown, "fit_markdown") and markdown.fit_markdown:
        return markdown.fit_markdown
    interno = getattr(resultado, "_markdown", None)
    if interno and getattr(interno, "fit_markdown", None):
        return interno.fit_markdown
    if interno and getattr(interno, "raw_markdown", None):
        return interno.raw_markdown
    return resultado.extracted_content or resultado.cleaned_html or ""


def texto_para_filtro(linea):
    return PREFIJO_ESTRUCTURA.sub("", linea).strip()


def linea_estructurada(linea):
    encabezado = re.match(r"^(#{1,6})\s+(.+)$", linea)
    if not encabezado:
        return linea.strip(" -*#|•·")
    texto = encabezado[2].strip(" -*#|•·")
    if not texto:
        return ""
    nivel = len(encabezado[1])
    if nivel == 1:
        return f"Título principal: {texto}"
    if nivel == 2:
        return f"Sección: {texto}"
    return f"Subsección: {texto}"


def linea_independiente(linea):
    return (
        linea.startswith(("Título principal:", "Sección:", "Subsección:", "Archivo complementario",
                         "Video complementario:", "Formulario complementario:", "Documento complementario:",
                         "Enlace complementario:"))
        or bool(re.match(r"^\d+[\).]\s+", linea))
        or bool(re.match(r"^[A-Za-zÁÉÍÓÚÜÑáéíóúüñ]\)\s+", linea))
        or "http://" in linea
        or "https://" in linea
    )


def cerrar_parrafo(lineas, buffer):
    if buffer:
        lineas.append(" ".join(buffer).strip())
        buffer.clear()


def compactar_lineas(lineas):
    compactas, buffer = [], []
    for linea in lineas:
        if linea_independiente(linea):
            cerrar_parrafo(compactas, buffer)
            compactas.append(linea)
            continue
        if len(linea) <= 85 and not linea.endswith((".", ":", ";", "?", "!", ")", "]")):
            buffer.append(linea)
        else:
            buffer.append(linea)
            cerrar_parrafo(compactas, buffer)
    cerrar_parrafo(compactas, buffer)
    return compactas


def limpiar_texto(texto):
    texto = unicodedata.normalize("NFKC", texto or "")
    texto = COMENTARIO_HTML.sub(" ", texto)
    texto = ADVERTENCIA_SISTEMA.sub(" ", texto)
    texto = EMOJI.sub(" ", texto)
    texto = re.sub(r"```+", " ", texto)
    texto = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", texto)

    def reemplazar_enlace_vacio(match):
        url = url_para_texto(match[1])
        return etiqueta_enlace_complementario(url) or " "

    texto = re.sub(r"\[\s*\]\((https?://[^\s)]+)(?:\s+\"[^\"]*\")?\)", reemplazar_enlace_vacio, texto)

    def reemplazar_enlace(match):
        etiqueta = re.sub(r"\s+", " ", match[1]).strip()
        url = url_para_texto(match[2])
        if not etiqueta or not url or not url_visible_util(url):
            return " "
        etiqueta_norm = etiqueta.casefold()
        if etiqueta_norm in {"previous", "next", "anterior", "siguiente"}:
            return " "
        if etiqueta_norm in {"click aqui", "clic aqui", "aqui", "aquí", "ver mas", "ver más"}:
            return url
        return f"{etiqueta}: {url}"

    texto = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)(?:\s+\"[^\"]*\")?\)", reemplazar_enlace, texto)
    texto = URL_EN_TEXTO.sub(
        lambda m: (url_para_texto(m[0]) if url_visible_util(url_para_texto(m[0])) else " "),
        texto,
    )
    texto = re.sub(r"<[^>]+>", " ", texto)
    texto = re.sub(r"[ \t\r\f\v]+", " ", texto)
    lineas, vistas = [], set()
    for linea in texto.splitlines():
        linea = linea_estructurada(re.sub(r"\s+", " ", linea))
        if "http://" not in linea and "https://" not in linea:
            linea = re.sub(r"(?<=[a-záéíóúüñ0-9])(?=[A-ZÁÉÍÓÚÜÑ])", " ", linea)
            linea = re.sub(r"(?<=[A-Za-zÁÉÍÓÚÜÑáéíóúüñ])(?=\d)|(?<=\d)(?=[A-Za-zÁÉÍÓÚÜÑáéíóúüñ])", " ", linea)
        if not linea or len(linea) < 3:
            continue
        filtro = texto_para_filtro(linea)
        if ("http://" in filtro or "https://" in filtro) and LINEA_MENU_INSTITUCIONAL.search(filtro):
            continue
        if (LINEA_NAVEGACION.search(filtro) or LINEA_RUIDO.search(filtro) or
                LINEA_FORMULARIO.search(filtro) or LINEA_BREADCRUMB.search(filtro)):
            continue
        if re.fullmatch(r"(?:\d+\s*){1,4}", filtro):
            continue
        if re.fullmatch(r"(?:descargar|más|ver todas?|contactos?|enlaces?|\w+\s*0\s+comments?)", filtro, re.I):
            continue
        clave = texto_para_filtro(linea).casefold()
        if clave in vistas:
            continue
        vistas.add(clave)
        lineas.append(linea)
    limpio = "\n".join(compactar_lineas(lineas))
    return re.sub(r"\n{3,}", "\n\n", limpio).strip()


def texto_indexable(texto):
    # A heading alone is not a document; short useful notices and link directories are.
    contenido = " ".join(line for line in (texto or "").splitlines() if not line.startswith("#"))
    return bool(contenido.strip()) and ("https://" in contenido or "http://" in contenido or
                                       len(re.findall(r"\w", contenido)) >= 30)


def partir(texto, chunk_chars, overlap):
    return structured_chunks(texto, chunk_chars, overlap)


def titulo(resultado, url, texto):
    meta = resultado.metadata or {}
    valor = (meta.get("title") or meta.get("og:title") or "").strip()
    if valor:
        return valor[:220]
    for linea in texto.splitlines():
        if 8 <= len(linea) <= 220:
            return linea
    return url


def hash_texto(texto):
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def coleccion(chroma_dir, nombre, reset=False):
    db = chromadb.PersistentClient(path=str(chroma_dir))
    if reset:
        try:
            db.delete_collection(nombre)
            print(f"Colección eliminada para reindexación limpia: {nombre}")
        except Exception:
            print(f"Colección inexistente o no eliminable; se creará/actualizará: {nombre}")
    return db.get_or_create_collection(
        nombre,
        metadata={"embedding_model": config.EMBEDDING_MODEL, "tipo": "web_unamba"},
        embedding_function=None,
    )


def embedding(client, texto):
    return client.embed(
        model=config.EMBEDDING_MODEL,
        input=texto,
        truncate=False,
        keep_alive=config.OLLAMA_KEEP_ALIVE,
    ).embeddings[0]


def pagina_indexada_sin_cambios(collection, url, page_hash, total):
    actual = collection.get(where={"url": url}, include=["metadatas"])
    metadatas = actual.get("metadatas") or []
    return len(metadatas) == total and all(m.get("page_hash") == page_hash and m.get("extractor_version") == VERSION for m in metadatas)


def limpiar_registros_no_web(collection):
    """Borra registros antiguos de documentos/login que pudieron quedar indexados."""
    result = collection.get(include=["metadatas"])
    urls = {
        meta.get("url")
        for meta in result.get("metadatas", [])
        if meta and meta.get("url") and (not es_url_publica(meta["url"]) or ERROR_PAGE.search(
            unicodedata.normalize("NFKD", meta.get("title", "")).encode("ascii", "ignore").decode()))
    }
    for url in sorted(urls):
        collection.delete(where={"url": url})
        print(f"Eliminada URL no web de Chroma: {url}")
    return len(urls)


def guardar_pagina(collection, client, url, title, domain, text, chunks, crawled_at=None):
    page_hash = hash_texto(json.dumps([VERSION, config.EMBEDDING_MODEL, title, text, chunks], ensure_ascii=False))
    if pagina_indexada_sin_cambios(collection, url, page_hash, len(chunks)):
        print(f"Sin cambios: {url}")
        return 0
    ids, docs, metas, vectors = [], [], [], []
    timestamp = datetime.now(timezone.utc).isoformat()
    for i, chunk in enumerate(chunks, 1):
        chunk_hash = hash_texto(f"{url}\n{i}\n{chunk}")
        ids.append(chunk_hash)
        docs.append(chunk)
        metas.append({
            "url": url,
            "title": title,
            "domain": domain,
            "chunk": i,
            "chunks": len(chunks),
            "page_hash": page_hash,
            "crawled_at": crawled_at or timestamp,
            "indexed_at": timestamp,
            "extractor_version": VERSION,
            "embedding_model": config.EMBEDDING_MODEL,
        })
        vectors.append(embedding(client, chunk))
    # Keep the previous page if embedding or upsert fails; delete obsolete IDs last.
    old_ids = collection.get(where={"url": url}, include=[])["ids"]
    collection.upsert(ids=ids, documents=docs, metadatas=metas, embeddings=vectors)
    stale = [item for item in old_ids if item not in set(ids)]
    if stale:
        collection.delete(ids=stale)
    print(f"Indexada: {url} ({len(chunks)} fragmentos)")
    return len(chunks)


def enlaces(resultado, base_url, dominios):
    links = (resultado.links or {}).get("internal", []) + (resultado.links or {}).get("external", [])
    for item in links:
        href = item.get("href") if isinstance(item, dict) else str(item)
        if not href:
            continue
        url = normalizar_url(urljoin(base_url, href))
        if url and permitido(url, dominios) and es_url_publica(url):
            yield url


async def rastrear(args):
    if args.collection == config.COLECCION_DOCS:
        raise ValueError("El indexador web no puede escribir en la coleccion de PDFs.")
    dominios = set(args.domains or config.WEB_ALLOWED_DOMAINS)
    semillas = [normalizar_url("https://" + d) for d in sorted(dominios)]
    pendientes = deque((url, 0) for url in semillas if url)
    vistos = set()
    collection = None if args.dry_run else coleccion(args.chroma_dir, args.collection, args.reset_collection)
    client = None if args.dry_run else ollama.Client(host=config.OLLAMA_HOST, timeout=600, trust_env=False)
    browser = BrowserConfig(headless=True, verbose=False)
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        only_text=False,
        remove_forms=False,
        exclude_external_links=False,
        page_timeout=args.timeout * 1000,
        word_count_threshold=0,
        delay_before_return_html=2,
        verbose=False,
    )
    paginas_ok = paginas_error = fragmentos = 0
    print(f"Colección: {args.collection}")
    print(f"Chroma: {args.chroma_dir}")
    print(f"Dominios permitidos: {', '.join(sorted(dominios))}")
    report = []
    borradas = limpiar_registros_no_web(collection) if collection is not None else 0
    if borradas:
        print(f"Limpieza inicial: {borradas} URLs no web eliminadas.")
    async with AsyncWebCrawler(config=browser) as crawler:
        while pendientes and (args.max_pages <= 0 or len(vistos) < args.max_pages):
            url, profundidad = pendientes.popleft()
            if url in vistos or not permitido(url, dominios) or not es_url_publica(url):
                continue
            vistos.add(url)
            total = "sin límite" if args.max_pages <= 0 else str(args.max_pages)
            print(f"[{len(vistos)}/{total}] Extrayendo: {url}")
            try:
                resultado = await crawler.arun(url=url, config=run_config)
            except Exception as exc:
                paginas_error += 1
                print(f"Error de extracción: {url} ({exc})")
                continue
            final_url = normalizar_url(resultado.redirected_url or resultado.url or url)
            status = getattr(resultado, "status_code", None)
            title, texto, invalid = extract_html(getattr(resultado, "html", "") or "", final_url or url)
            if status in (404, 410) or invalid:
                if collection is not None:
                    for obsolete in {url, final_url} - {""}:
                        collection.delete(where={"url": obsolete})
                print(f"Pagina inexistente excluida: {url}")
                report.append({"url": url, "status": status, "resultado": "pagina_inexistente"})
                continue
            if (not resultado.success or not final_url or not permitido(final_url, dominios)
                    or not es_url_publica(final_url) or (status and status >= 400)):
                paginas_error += 1
                print(f"Omitida: {url} ({resultado.error_message or 'fuera de dominio, login, documento o inaccesible'})")
                continue
            # DOM extraction preserves short numeric values, repeated labels and exact hrefs.
            texto = "\n".join(line for line in texto.splitlines()
                              if not LINEA_RUIDO.search(line.replace("api.whatsapp.com", "whatsapp.com")))
            util = texto_indexable(texto)
            if util and title:
                texto = "# " + title + "\n" + texto
            chunks = partir(texto, args.chunk_chars, args.overlap) if util else []
            report.append({"url": final_url, "titulo": title, "status": status,
                           "fragmentos": len(chunks), "texto": texto, "extractor_version": VERSION})
            if args.dry_run:
                paginas_ok += bool(chunks)
                fragmentos += len(chunks)
            elif chunks:
                try:
                    fragmentos += guardar_pagina(
                        collection, client, final_url, title or titulo(resultado, final_url, texto),
                        dominio(final_url), texto, chunks,
                    )
                    paginas_ok += 1
                    if final_url != url:
                        collection.delete(where={"url": url})
                except Exception as exc:
                    paginas_error += 1
                    print(f"Error al indexar; se conserva lo anterior: {final_url} ({exc})")
            else:
                print(f"Omitida sin texto; se conserva captura anterior: {final_url}")
            if profundidad < args.max_depth:
                for link in enlaces(resultado, final_url, dominios):
                    if link not in vistos:
                        pendientes.append((link, profundidad + 1))
    print("\nResultado:")
    print(f"Páginas visitadas: {len(vistos)}")
    print(f"Páginas indexadas o sin cambios: {paginas_ok}")
    print(f"Fragmentos nuevos/actualizados: {fragmentos}")
    print(f"Errores: {paginas_error}")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def reindexar_capturas(args):
    """Aplica el extractor a una captura auditada sin volver a rastrear ni resetear."""
    if args.collection == config.COLECCION_DOCS or args.reset_collection:
        raise ValueError("No se permite reset ni coleccion PDF al importar capturas web.")
    manifest = json.loads(args.captures.read_text(encoding="utf-8"))
    collection = None if args.dry_run else coleccion(args.chroma_dir, args.collection)
    client = None if args.dry_run else ollama.Client(host=config.OLLAMA_HOST, timeout=600, trust_env=False)
    if collection is not None and args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        backup = args.report.with_name(args.report.stem + "_antes.json")
        if not backup.exists():
            backup.write_text(json.dumps(collection.get(include=["documents", "metadatas"]),
                                         ensure_ascii=False, indent=2), encoding="utf-8")
    results = []
    for i, page in enumerate(manifest, 1):
        original = normalizar_url(page["url"])
        url = normalizar_url(page.get("final_url") or original)
        state = {"url": original, "final_url": url}
        try:
            if not permitido(original, config.WEB_ALLOWED_DOMAINS) or not permitido(url, config.WEB_ALLOWED_DOMAINS):
                raise ValueError("Dominio fuera del alcance web")
            if not es_url_publica(original) or not es_url_publica(url):
                if collection is not None:
                    collection.delete(where={"url": original})
                state["resultado"] = "excluida_acceso"
            elif not page.get("success") or (page.get("status") or 0) >= 500:
                state["resultado"] = "captura_fallida_conservada"
            else:
                key = page["key"]
                if not re.fullmatch(r"[a-f0-9]{64}", key):
                    raise ValueError("Clave de captura invalida")
                html = (args.captures.parent / (key + ".html")).read_text(encoding="utf-8")
                title, text, invalid = extract_html(html, url)
                if invalid or page.get("status") in (404, 410):
                    if collection is not None:
                        for obsolete in {url, original}:
                            collection.delete(where={"url": obsolete})
                    state["resultado"] = "pagina_inexistente"
                elif page.get("status") and page["status"] >= 400:
                    state["resultado"] = "captura_fallida_conservada"
                elif not text:
                    state["resultado"] = "vacia_conservada"
                elif not texto_indexable(text):
                    if collection is not None:
                        collection.delete(where={"url": original})
                    state["resultado"] = "sin_contenido_sustantivo"
                else:
                    text = "\n".join(l for l in text.splitlines() if not LINEA_RUIDO.search(l.replace("api.whatsapp.com", "whatsapp.com")))
                    text = ("# " + title + "\n" if title else "") + text
                    chunks = partir(text, args.chunk_chars, args.overlap)
                    if collection is not None:
                        guardar_pagina(collection, client, url, title or url, dominio(url), text, chunks, page.get("captured_at"))
                        if original != url:
                            collection.delete(where={"url": original})
                    state.update(resultado="validada" if args.dry_run else "actualizada", fragmentos=len(chunks))
        except Exception as exc:
            state.update(resultado="error", error=str(exc))
        results.append(state)
        print(f"[{i}/{len(manifest)}] {state['resultado']}: {original}", flush=True)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    errors = sum(r["resultado"] == "error" for r in results)
    print(f"Capturas procesadas: {len(results)}. Errores: {errors}")
    return 1 if errors else 0


def argumentos():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chroma-dir", type=Path, default=config.CHROMA_DIR)
    parser.add_argument("--collection", default=config.COLECCION_WEB)
    parser.add_argument("--max-pages", type=int, default=0,
                        help="Máximo de páginas a visitar. Usa 0 para no imponer límite.")
    parser.add_argument("--max-depth", type=int, default=config.WEB_CRAWL_MAX_DEPTH)
    parser.add_argument("--chunk-chars", type=int, default=config.WEB_CHUNK_CHARS)
    parser.add_argument("--overlap", type=int, default=config.WEB_CHUNK_OVERLAP)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--domains", nargs="*", help="Subconjunto de dominios permitidos para probar.")
    parser.add_argument("--reset-collection", action="store_true",
                        help="Elimina la colección web antes de rastrear para evitar restos de índices antiguos.")
    parser.add_argument("--dry-run", action="store_true", help="Extrae sin escribir Chroma ni generar embeddings.")
    parser.add_argument("--report", type=Path, help="Guarda textos extraidos y resultados como JSON.")
    parser.add_argument("--captures", type=Path, help="Reprocesa un manifiesto capturas.json auditado; no rastrea otra vez.")
    args = parser.parse_args()
    if args.domains:
        no_permitidos = sorted(set(args.domains) - set(config.WEB_ALLOWED_DOMAINS))
        if no_permitidos:
            parser.error("Dominios no permitidos: " + ", ".join(no_permitidos))
    if args.overlap < 0 or args.chunk_chars <= args.overlap:
        parser.error("--chunk-chars debe ser mayor que --overlap.")
    return args


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = argumentos()
    if args.captures:
        raise SystemExit(reindexar_capturas(args))
    asyncio.run(rastrear(args))


if __name__ == "__main__":
    main()
