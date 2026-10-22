import os
import argparse
import json
import hashlib
import re
import time
import sys
from pathlib import Path
from urllib.parse import urlparse, urldefrag

from firecrawl import Firecrawl

from motores.portal_unamba.src.config import RAW_DATA_DIR, DATA_DIR
from compartido.documentos import atomic_write
from motores.portal_unamba.extraer_enlaces import main as descubrir_enlaces, permitido
from motores.portal_unamba.src.ingestion.url_policy import POLICY

# Carpeta donde se guardarán los Markdown
CARPETA_SALIDA = RAW_DATA_DIR


def nombre_seguro(url: str) -> str:
    """Convierte una URL en un nombre de archivo seguro."""
    parsed = urlparse(url)
    ruta = parsed.path.strip("/").replace("/", "_")
    nombre = f"{parsed.netloc}_{ruta}" if ruta else parsed.netloc
    nombre = re.sub(r"[^a-zA-Z0-9_\-\.]", "_", nombre)
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return nombre[:100] + "_" + digest + ".md"


def guardar_markdown(url: str, markdown: str) -> Path:
    CARPETA_SALIDA.mkdir(parents=True, exist_ok=True)
    archivo = CARPETA_SALIDA / nombre_seguro(url)

    contenido = f"""---
url: {url}
extraido_con: Firecrawl
formato: markdown
---

# Contenido extraído

{markdown}
"""

    atomic_write(archivo, contenido)
    return archivo


# Espaciado conservador para el limite observado en esta cuenta.
INTERVALO_SEGUNDOS = 7
MAX_INTENTOS = 4


def extraer_urls(urls: list[str], *, force: bool = False, before_write=None) -> None:
    if any(not permitido(url) for url in urls):
        raise ValueError('Solo se permite extraer HTML de www.unamba.edu.pe o unamba.edu.pe; no se permiten subdominios ni páginas técnicas.')
    authorized = set(urls_descubiertas(DATA_DIR / 'crawl' / 'enlaces_validos.json'))
    if not set(urls) <= authorized:
        raise ValueError('Hay URLs que no se comprobaron como accesibles desde la interfaz. Ejecuta primero el rastreo de UNAMBA.')
    api_key = os.getenv("FIRECRAWL_API_KEY", "").strip()
    if not api_key:
        raise ValueError("Configura FIRECRAWL_API_KEY en .env antes de extraer páginas.")
    CARPETA_SALIDA.mkdir(parents=True, exist_ok=True)
    cliente = Firecrawl(api_key=api_key)
    exitosos = fallidos = omitidos = 0
    proxima_peticion = 0.0
    interrumpido = False

    try:
        for i, url in enumerate(urls, start=1):
            archivo = CARPETA_SALIDA / nombre_seguro(url)
            if archivo.exists() and not force:
                contenido = archivo.read_text(encoding="utf-8")
                cabecera = f"---\nurl: {url}\n"
                cuerpo = contenido.partition("# Contenido extraído\n")[2]
                if contenido.startswith(cabecera) and cuerpo.strip():
                    print(f"[{i}/{len(urls)}] Ya guardado: {archivo}", flush=True)
                    omitidos += 1
                    continue

            print(f"[{i}/{len(urls)}] Extrayendo: {url}", flush=True)
            for intento in range(1, MAX_INTENTOS + 1):
                time.sleep(max(0, proxima_peticion - time.monotonic()))
                proxima_peticion = time.monotonic() + INTERVALO_SEGUNDOS
                try:
                    resultado = cliente.scrape(url, formats=["markdown"])
                    metadata = getattr(resultado, 'metadata', None)
                    final = (metadata.get('sourceURL') or metadata.get('url')) if isinstance(metadata, dict) else (getattr(metadata, 'source_url', None) or getattr(metadata, 'url', None))
                    if final and not permitido(final):
                        raise ValueError('La extracción fue redirigida fuera del portal permitido. No se guardó el documento.')
                    markdown = getattr(resultado, "markdown", None)
                    if not markdown or not markdown.strip():
                        print("  Sin contenido Markdown.", flush=True)
                        fallidos += 1
                        break
                    if before_write is not None:
                        before_write(archivo)
                    archivo = guardar_markdown(url, markdown)
                    print(f"  Guardado: {archivo}", flush=True)
                    exitosos += 1
                    break
                except Exception as error:
                    mensaje = str(error)
                    limite = "rate limit" in mensaje.lower() or "429" in mensaje
                    if limite and intento < MAX_INTENTOS:
                        indicado = re.search(r"retry after\s+(\d+(?:\.\d+)?)s", mensaje, re.I)
                        espera = float(indicado.group(1)) + 2 if indicado else 65
                        proxima_peticion = max(proxima_peticion, time.monotonic() + espera)
                        print(f"  Limite alcanzado. Esperando {espera:.0f} s antes del reintento {intento + 1}/{MAX_INTENTOS}.", flush=True)
                        continue
                    print(f"  Error en {url}: {mensaje}", flush=True)
                    fallidos += 1
                    break
    except KeyboardInterrupt:
        interrumpido = True
        print("\nExtraccion detenida. Puedes ejecutar de nuevo para continuar.", flush=True)

    print("\n==============================")
    print("Extraccion interrumpida" if interrumpido else "Extraccion finalizada")
    print(f"Correctas nuevas: {exitosos}")
    print(f"Ya guardadas:     {omitidos}")
    print(f"Fallidas:         {fallidos}")
    print(f"Carpeta:          {CARPETA_SALIDA.resolve()}")
    print("==============================")
    if interrumpido:
        raise SystemExit(130)
    if fallidos:
        raise SystemExit(1)


def urls_descubiertas(path: Path) -> list[str]:
    """Selecciona páginas HTML finales, sin anclas ni duplicados."""
    resumen = json.loads(path.read_text(encoding="utf-8"))
    if resumen.get('policy') != POLICY:
        raise ValueError('La lista de enlaces es anterior al nuevo alcance. Ejecuta otra vez el rastreo desde la interfaz.')
    urls = []
    for item in resumen["enlaces"]:
        if item.get("tipo") not in {"text/html", "application/xhtml+xml"}:
            continue
        url = urldefrag(item.get("url_final") or item["url"])[0]
        if permitido(url) and url not in urls:
            urls.append(url)
    if not urls:
        raise ValueError("El rastreo no encontró páginas HTML válidas para extraer.")
    return urls


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Descubre enlaces de UNAMBA y extrae sus páginas automáticamente.")
    parser.add_argument("--max-paginas", type=int, default=int(os.getenv("PORTAL_CRAWL_MAX_PAGES", "200")), help="Máximo de destinos a verificar; 0 = sin límite.")
    parser.add_argument("--timeout", type=float, default=float(os.getenv("PORTAL_CRAWL_TIMEOUT", "25")))
    args = parser.parse_args(argv)
    if args.max_paginas < 0:
        parser.error("--max-paginas no puede ser negativo")
    if not os.getenv("FIRECRAWL_API_KEY", "").strip():
        raise SystemExit("Configura FIRECRAWL_API_KEY en .env antes de extraer páginas.")
    salida = DATA_DIR / "crawl"
    descubrir_enlaces(["--salida", str(salida), "--max-paginas", str(args.max_paginas),
                       "--timeout", str(args.timeout)])
    urls = urls_descubiertas(salida / "enlaces_validos.json")
    print(f"Páginas únicas a extraer: {len(urls)}", flush=True)
    extraer_urls(urls)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    main()
