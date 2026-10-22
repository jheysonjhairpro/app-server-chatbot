"""Consulta Chroma como consultar_pdfs.py y añade enlaces verificados de gob.pe.

python -m motores.pdf.consultar_pdfs_gobpe --pregunta "Qué dice la resolución 218-2026"
Acepta los mismos argumentos y comandos que consultar_pdfs.py.
Opcionales: --gobpe-paginas 3 --gobpe-timeout 10 --sin-gobpe
La web sólo localiza enlaces: nunca aporta contenido a la respuesta de Ollama.
"""
import argparse
import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import time
import unicodedata
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit, unquote
from urllib.request import Request, urlopen

BASE = "https://www.gob.pe"
RUTA = "/institucion/universidad-nacional-micaela-bastidas-de-apurimac/normas-legales/"
LISTADO = BASE + RUTA + "tipos/26-resolucion-directoral"


def normalizar(texto):
    return "".join(c for c in unicodedata.normalize("NFD", texto.lower())
                   if unicodedata.category(c) != "Mn")


def enlace_norma(url):
    """Admite únicamente fichas de normas de esta institución."""
    p = urlsplit(url)
    return (p.scheme == "https" and p.netloc == "www.gob.pe"
            and re.fullmatch(re.escape(RUTA) + r"\d+-[^/]+", p.path) is not None)


class Enlaces(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.enlaces = {}
        self.actual = None
        self.textos = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href", "")
            url = urljoin(BASE, href)
            self.actual = url if enlace_norma(url) else None
            self.textos = []

    def handle_data(self, data):
        if self.actual:
            self.textos.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.actual:
            texto = " ".join(" ".join(self.textos).split())
            if len(texto) > len(self.enlaces.get(self.actual, "")):
                self.enlaces[self.actual] = texto
            self.actual = None


def identidad(nombre):
    """Usa el nombre del PDF, no resoluciones citadas en su contenido."""
    nombre = normalizar(nombre.replace("\\", "/").rsplit("/", 1)[-1])
    numero = re.search(r"(?<!\d)(\d{1,4})[-_](20\d{2})(?!\d)", nombre)
    # Algunos archivos usan «n-0-218-2026»; el número detectado es 218.
    return (int(numero[1]), numero[2]) if numero else None


def elegir(enlaces, nombre):
    archivo = nombre.replace("\\", "/").rsplit("/", 1)[-1]
    identificador = re.match(r"(\d{6,})-", archivo)
    if identificador:
        exactos = [url for url in enlaces
                   if urlsplit(url).path.rsplit("/", 1)[-1].startswith(identificador[1] + "-")]
        if len(exactos) == 1:
            return exactos[0]
        if exactos:
            return None
    clave = identidad(archivo)
    if not clave or "directoral" not in normalizar(archivo) or "diga" not in normalizar(archivo):
        return None
    candidatos = []
    for url, titulo in enlaces.items():
        slug = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
        texto = normalizar(titulo + " " + slug)
        numeros = {(int(n), a) for n, a in
                   re.findall(r"(?<!\d)(\d{1,4})\s*[-_/]\s*(20\d{2})(?!\d)", texto)}
        if clave in numeros and "diga" in texto and ("directoral" in texto or not titulo):
            candidatos.append(url)
    return candidatos[0] if len(candidatos) == 1 else None


def descargar(url, timeout):
    req = Request(url, headers={"User-Agent": "UNAMBA-ConsultaDocumental/1.0",
                                "Accept": "text/html"})
    with urlopen(req, timeout=timeout) as respuesta:
        destino = urlsplit(respuesta.geturl())
        if destino.scheme != "https" or destino.netloc != "www.gob.pe":
            raise RuntimeError("gob.pe redirigió fuera del portal esperado.")
        datos = respuesta.read(5_000_001)
        if len(datos) > 5_000_000:
            raise RuntimeError("La página supera el límite de descarga.")
        return datos.decode(respuesta.headers.get_content_charset() or "utf-8", errors="replace")


class BuscadorGobPe:
    def __init__(self, paginas=3, timeout=10, descargar_html=descargar):
        self.paginas = paginas
        self.timeout = timeout
        self.descargar = descargar_html
        self.enlaces = {}
        self.cache = {}
        self.visitados = set()

    def buscar(self, nombre):
        if nombre in self.cache:
            return self.cache[nombre]
        encontrado = elegir(self.enlaces, nombre)
        if encontrado:
            return encontrado, ""
        clave = identidad(nombre)
        termino = f"{clave[0]}-{clave[1]}" if clave else ""
        urls = ([LISTADO + "?" + urlencode({"term": termino, "page": pagina})
                 for pagina in range(1, self.paginas + 1)] if termino else [LISTADO])
        errores = []
        limite = time.monotonic() + 25
        for url in urls:
            restante = limite - time.monotonic()
            if restante <= 0:
                errores.append("Se alcanzó el límite de tiempo de búsqueda.")
                break
            if url in self.visitados:
                continue
            try:
                parser = Enlaces()
                parser.feed(self.descargar(url, min(self.timeout, restante)))
                self.enlaces.update(parser.enlaces)
                self.visitados.add(url)
                encontrado = elegir(self.enlaces, nombre)
                if encontrado:
                    self.cache[nombre] = (encontrado, "")
                    return self.cache[nombre]
                # El filtro puede devolver cero resultados: se revisa también el listado.
                if not parser.enlaces:
                    break
            except Exception as exc:
                errores.append(str(exc))
                break
        if not encontrado and LISTADO not in self.visitados and not errores:
            try:
                restante = limite - time.monotonic()
                if restante <= 0:
                    raise RuntimeError("Se alcanzó el límite de tiempo de búsqueda.")
                parser = Enlaces()
                parser.feed(self.descargar(LISTADO, min(self.timeout, restante)))
                self.enlaces.update(parser.enlaces)
                self.visitados.add(LISTADO)
                encontrado = elegir(self.enlaces, nombre)
            except Exception as exc:
                errores.append(str(exc))
        motivo = ("No se pudo consultar gob.pe: " + "; ".join(errores)) if errores else (
            "No se encontró una coincidencia única en las páginas revisadas.")
        resultado = (encontrado, "" if encontrado else motivo)
        # No conserva fallos de red: la próxima consulta puede reintentarlo.
        if not errores:
            self.cache[nombre] = resultado
        return resultado

    def mostrar(self, paginas):
        documentos = {}
        for pagina in paginas:
            documentos.setdefault(pagina.source, pagina.nombre)
        print("\nEnlaces oficiales:")
        for nombre in documentos.values():
            print(f"Buscando en gob.pe: {nombre}...", flush=True)
            url, motivo = self.buscar(nombre)
            if url:
                print(f"{nombre}\nPara más información, visita el enlace: {url}")
            else:
                print(f"{nombre}: {motivo}")
                print(f"Puedes revisar el listado oficial: {LISTADO}")


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--gobpe-paginas", type=int, default=3)
    parser.add_argument("--gobpe-timeout", type=float, default=10)
    parser.add_argument("--sin-gobpe", action="store_true")
    extras, restantes = parser.parse_known_args()
    if not 1 <= extras.gobpe_paginas <= 10:
        parser.error("--gobpe-paginas debe estar entre 1 y 10.")
    if not 0 < extras.gobpe_timeout <= 30:
        parser.error("--gobpe-timeout debe ser mayor que 0 y menor o igual que 30.")
    from motores.pdf import consultar_pdfs as original
    buscador = BuscadorGobPe(extras.gobpe_paginas, extras.gobpe_timeout)
    referencias_originales = original.referencias

    def referencias_con_enlaces(paginas):
        referencias_originales(paginas)
        if not extras.sin_gobpe:
            buscador.mostrar(paginas)

    argv_original = sys.argv[:]
    try:
        sys.argv = [sys.argv[0]] + restantes
        original.referencias = referencias_con_enlaces
        original.main()
    finally:
        original.referencias = referencias_originales
        sys.argv = argv_original


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nChat cerrado.")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

