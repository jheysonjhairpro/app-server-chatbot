"""Contexto documental verificable, sin dependencias de modelos ni acceso al PDF."""
import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path


def normalizar(texto):
    return "".join(c for c in unicodedata.normalize("NFD", texto.casefold())
                   if unicodedata.category(c) != "Mn")


def palabras(texto):
    return re.findall(r"[a-z0-9]+", normalizar(texto))


STOP = set("a al algo como con cual cuales cuando cuantos cuantas de del el ella en "
           "es esa ese esta este esto hay la las lo los me mi para por que se si son "
           "su sus tiene un una unos unas y pdf documento dime mencioname favor".split())


@dataclass(frozen=True)
class Pagina:
    source: str
    nombre: str
    numero: int
    texto: str

    @property
    def clave(self):
        return self.source, self.numero


@dataclass
class Documento:
    paginas: list
    completo: bool
    problema: str = ""


@dataclass(frozen=True)
class ReferenciaDocumento:
    tipo: str
    numero: int
    anio: str = ""


TIPOS_DOCUMENTO = r"resolucion|informe|oficio|directiva|ley"
REFERENCIA = re.compile(
    r"\b(" + TIPOS_DOCUMENTO + r")\b"
    r"(?:[\s_-]+(?:rectoral|directoral|tecnico|tecnica|de|del|consejo|universitario|directivo|ministerial|jefatural)){0,5}"
    r"[\s_(-]*(?:(?:numero|nro\.?|num\.?|n[.°ºo]*)(?:[\s°º._(-]*))?"
    r"(\d{1,6})(?!\d)"
    r"(?:\s*(?:[-/–]|del?\s+(?:ano\s+)?)\s*(\d{4})(?!\d))?"
)


def referencias_documentales(texto):
    q = normalizar(texto).replace("_", " ")
    # Algunos nombres publicados separan el cero inicial: n-0-218-2026.
    q = re.sub(r"(?<!\d)0+\s*-\s*(\d{1,4})\s*-\s*((?:19|20|21)\d{2})(?!\d)",
               r"\1-\2", q)
    explicitas = [ReferenciaDocumento(m[1], int(m[2]), m[3] or "") for m in REFERENCIA.finditer(q)]
    if explicitas:
        return list(dict.fromkeys(explicitas))
    return list(dict.fromkeys(ReferenciaDocumento("", int(m[1]), m[2]) for m in re.finditer(
        r"\b(\d{1,4})[-/](\d{4})\b", q)
        if not (1900 <= int(m[1]) <= 2199 and 1900 <= int(m[2]) <= 2199)))


def titulo_documental(texto):
    """Lee el encabezado; las resoluciones citadas en antecedentes no son el título."""
    lineas = texto.splitlines()[:35]
    for i, linea in enumerate(lineas):
        limpia = linea.strip().strip("*# ")
        if re.match(r"(?:vistos|considerando|asunto)\b", normalizar(limpia)):
            break
        if not re.match(r"(?:" + TIPOS_DOCUMENTO + r")\b", normalizar(limpia)):
            continue
        candidato = limpia
        if not REFERENCIA.search(normalizar(candidato)) and i + 1 < len(lineas):
            candidato += " " + lineas[i + 1].strip()
        if REFERENCIA.match(normalizar(candidato)):
            return candidato[:200]
    return ""


def unir_fragmentos(partes):
    """Reconstruye el solapamiento literal del indexador sin duplicar artículos."""
    texto = partes[0] if partes else ""
    for siguiente in partes[1:]:
        # strip() en el indexador puede quitar espacios al límite del solapamiento.
        union = None
        for cantidad in range(min(400, len(texto), len(siguiente)), 19, -1):
            if texto.endswith(siguiente[:cantidad]):
                union = cantidad
                break
        texto += (siguiente[union:] if union is not None else "\n" + siguiente)
    return texto


def reconstruir(registros):
    grupos = defaultdict(list)
    hashes, totales, totales_paginas = set(), set(), set()
    for texto, meta in registros:
        if not texto or not meta:
            continue
        cuerpo = re.sub(r"\ADOCUMENTO: [^\n]*\nPÁGINA: \d+\n\n", "", texto)
        grupos[int(meta["page"])].append((int(meta["chunk"]), cuerpo, meta))
        hashes.add(meta.get("sha256"))
        totales.add(meta.get("total_chunks"))
        if meta.get("total_pages"):
            totales_paginas.add(int(meta["total_pages"]))
    paginas = []
    completo = bool(grupos) and len(hashes) == 1 and None not in hashes
    completo &= len(totales) == 1 and len(registros) in totales
    numeros = sorted(grupos)
    completo &= numeros == list(range(1, max(numeros, default=0) + 1))
    if totales_paginas:
        completo &= totales_paginas == {len(grupos)}
    for numero in numeros:
        partes = sorted(grupos[numero], key=lambda x: x[0])
        completo &= [x[0] for x in partes] == list(range(1, len(partes) + 1))
        meta = partes[0][2]
        paginas.append(Pagina(meta["source"], meta["file_name"], numero,
                              unir_fragmentos([x[1] for x in partes])))
    return Documento(paginas, bool(completo), "" if completo else
                     "Índice incompleto o con versiones mezcladas; vuelve a indexar este PDF.")


class Catalogo:
    def __init__(self, collection):
        self.collection = collection
        self.cache = {}
        archivos = {}
        titulos = {}
        for offset in range(0, collection.count(), 500):
            result = collection.get(limit=500, offset=offset, include=["metadatas"])
            for meta in result["metadatas"]:
                if meta and meta.get("source"):
                    archivos[meta["source"]] = meta.get("file_name", meta["source"])
                    if meta.get("document_title"):
                        titulos[meta["source"]] = meta["document_title"]
        self.archivos = sorted(archivos.items(), key=lambda x: (x[1].casefold(), x[0]))
        # Compatibilidad con índices antiguos: una lectura por lotes de cabeceras,
        # sin cargar cuerpos completos ni vectores, y sin escribir en la base.
        if len(titulos) < len(archivos):
            offset = 0
            while True:
                result = collection.get(where={"$and": [{"page": 1}, {"chunk": 1}]},
                                        limit=500, offset=offset, include=["documents", "metadatas"])
                for texto, meta in zip(result["documents"], result["metadatas"]):
                    if meta and meta["source"] not in titulos:
                        titulo = titulo_documental(texto or "")
                        if titulo:
                            titulos[meta["source"]] = titulo
                if len(result["ids"]) < 500:
                    break
                offset += 500
        self.titulos = titulos
        self.identidades = {}
        self.por_numero = defaultdict(list)
        for source, nombre in self.archivos:
            referencias = list(dict.fromkeys(
                referencias_documentales(titulos.get(source, ""))
                + referencias_documentales(Path(nombre).stem)))
            self.identidades[source] = referencias
            for ref in referencias:
                self.por_numero[ref.numero].append((source, ref))

    def etiqueta(self, source):
        return self.titulos.get(source) or dict(self.archivos)[source]

    def documento(self, source):
        if source not in self.cache:
            registros, offset = [], 0
            while True:
                result = self.collection.get(where={"source": source}, limit=500,
                                             offset=offset, include=["documents", "metadatas"])
                registros.extend(zip(result["documents"], result["metadatas"]))
                if len(result["ids"]) < 500:
                    break
                offset += 500
            self.cache[source] = reconstruir(registros)
        return self.cache[source]

    def detectar(self, pregunta, solo_explicitos=False, preferido=None):
        """Devuelve coincidencias explícitas; la ambigüedad nunca elige el primer PDF."""
        q = " ".join(palabras(pregunta))
        exactos = []
        for source, nombre in self.archivos:
            stem = Path(nombre).stem
            aliases = {stem, re.sub(r"^\d{5,}[-_ ]+", "", stem)}
            if any(len(" ".join(palabras(alias))) >= 5 and
                   f" {' '.join(palabras(alias))} " in f" {q} " for alias in aliases) or (
                   f" {' '.join(palabras(nombre))} " in f" {q} "):
                exactos.append(source)
        if exactos:
            nombres = {n.casefold() for s, n in self.archivos if s in exactos}
            if preferido in exactos and len(nombres) == 1:
                return [preferido]
            return exactos
        identificadores = referencias_documentales(pregunta)
        if identificadores:
            candidatos = list(dict.fromkeys(s for buscado in identificadores
                for s, encontrado in self.por_numero.get(buscado.numero, [])
                if (not buscado.tipo or not encontrado.tipo or buscado.tipo == encontrado.tipo) and
                   (not buscado.anio or buscado.anio == encontrado.anio)))
            if len(candidatos) > 1 and len(identificadores) == 1:
                etiquetas = {s: set(palabras(self.etiqueta(s))) for s in candidatos}
                compartidas = set.intersection(*etiquetas.values())
                consulta = set(palabras(pregunta))
                scores = {s: len({w for w in terminos - compartidas - STOP
                                 if len(w) >= 3 and not w.isdigit()} & consulta)
                          for s, terminos in etiquetas.items()}
                mejor = max(scores.values())
                if mejor:
                    candidatos = [s for s in candidatos if scores[s] == mejor]
            if len(identificadores) == 1 and preferido in candidatos:
                return [preferido]
            return candidatos
        if solo_explicitos:
            return []
        # Sólo palabras distintivas del nombre, nunca años o términos compartidos.
        nombres = [set(palabras(Path(n).stem)) - STOP for _, n in self.archivos]
        frecuencia = Counter(w for nombre in nombres for w in nombre)
        consulta = set(palabras(pregunta)) - STOP
        return [s for (s, _), nombre in zip(self.archivos, nombres)
                if any(len(w) >= 5 and not w.isdigit() and frecuencia[w] == 1
                       for w in consulta & nombre)]


def ranking_lexico(paginas, pregunta):
    """BM25 por página: los nombres del archivo no contaminan el ranking."""
    terminos = set(palabras(pregunta)) - STOP
    documentos = [Counter(palabras(p.texto)) for p in paginas]
    largos = [sum(d.values()) for d in documentos]
    promedio = sum(largos) / max(1, len(largos)) or 1
    frecuencia = Counter(t for d in documentos for t in terminos if t in d)
    scores = []
    for pagina, doc, largo in zip(paginas, documentos, largos):
        score = 0.0
        for termino in terminos:
            tf = doc[termino]
            if tf:
                idf = math.log(1 + (len(paginas) - frecuencia[termino] + .5) /
                               (frecuencia[termino] + .5))
                score += idf * tf * 2.5 / (tf + 1.5 * (.25 + .75 * largo / promedio))
        if score:
            scores.append((score, pagina))
    return [p for _, p in sorted(scores, key=lambda x: (-x[0], x[1].source, x[1].numero))]


def fusionar(*rankings):
    scores, paginas = defaultdict(float), {}
    for ranking in rankings:
        vistos = set()
        for posicion, pagina in enumerate(ranking, 1):
            if pagina.clave not in vistos:
                scores[pagina.clave] += 1 / (60 + posicion)
                paginas[pagina.clave] = pagina
                vistos.add(pagina.clave)
    return [paginas[k] for k in sorted(scores, key=lambda k: (-scores[k], k))]


def contexto(paginas):
    return "\n\n".join(f"[{i}] {p.nombre} — página {p.numero}\n{p.texto}"
                       for i, p in enumerate(paginas, 1))


def dividir_paginas(paginas, limite):
    """Ventanas para revisar TODO un documento largo, conservando las páginas."""
    lotes, lote, largo = [], [], 0
    for p in paginas:
        # Deja margen para encabezados y referencias; nunca corta el documento global.
        paso = max(200, limite - len(p.nombre) - 150)
        for inicio in range(0, len(p.texto), paso):
            parte = Pagina(p.source, p.nombre, p.numero, p.texto[inicio:inicio + paso])
            costo = len(contexto([parte])) + 20
            if lote and largo + costo > limite:
                lotes.append(lote)
                lote, largo = [], 0
            lote.append(parte)
            largo += costo
    if lote:
        lotes.append(lote)
    return lotes


def exhaustiva(pregunta):
    return bool(re.search(r"\b(cuantos|cuantas|todos|todas|total|enumera\w*|lista\w*|"
                          r"menciona\w*|resum\w*|completo|completa|general)\b",
                          normalizar(pregunta)))


ORDINALES = dict(zip("primero segundo tercero cuarto quinto sexto septimo octavo noveno "
                     "decimo undecimo duodecimo".split(), range(1, 13)))
PATRON_ARTICULO = re.compile(
    r"(?im)^\s*(?:\*\*)?art[ií]culo\s+(" + "|".join(ORDINALES) +
    r"|s[eé]ptimo|d[eé]cimo|und[eé]cimo|duod[eé]cimo|\d{1,3})[º°ª]?\s*[.：:\-–]"
)


def contar_articulos(pregunta, documento):
    """Sólo emite totales para una resolución completa con secuencia y cierre."""
    q = normalizar(pregunta)
    simple = re.search(r"\bcuantos\s+articulos\s+(?:tiene|hay|contiene|son|conforman)\b", q)
    if not simple or re.search(r"\b(sobre|refer\w*|mencion\w*|que|cuales|explic\w*|"
                               r"resum\w*|detall\w*|y|no|excepto|exclu\w*)\b", q):
        return None
    if not documento.completo:
        return None
    texto = "\n".join(p.texto for p in documento.paginas)
    aperturas = list(re.finditer(r"\bse\s+resuelve\s*:", texto, re.I))
    cierres = list(re.finditer(r"\breg[ií]strese[,\s]+comun[ií]quese\s+y\s+arch[ií]vese", texto, re.I))
    if len(aperturas) != 1 or len(cierres) != 1:
        return None
    inicio, final = aperturas[0].end(), cierres[0].start()
    if final <= inicio:
        return None
    encabezados = list(PATRON_ARTICULO.finditer(texto))
    internos = [m for m in encabezados if inicio <= m.start() < final]
    if not internos or len(internos) != len(encabezados):
        return None
    numeros = [int(m[1]) if m[1].isdigit() else ORDINALES[normalizar(m[1])]
               for m in internos]
    if numeros != list(range(1, len(numeros) + 1)):
        return None
    referencias, nombres = [], []
    for p in documento.paginas:
        matches = list(PATRON_ARTICULO.finditer(p.texto))
        if matches:
            referencias.append(p)
            nombres.extend(normalizar(m[1]) for m in matches)
    citas = " ".join(f"[{i}]" for i in range(1, len(referencias) + 1))
    respuesta = (f"La resolución tiene {len(numeros)} artículos: " +
                 ", ".join(nombres) + f". {citas}")
    return respuesta, referencias


def articulos_solicitados(pregunta):
    """Distingue 'los 6 artículos' (cantidad) de 'artículos 3,4' (identificadores)."""
    q = normalizar(pregunta)
    inicio = re.search(r"\barticulos?\s+(?:(?:del|el|los|numero|numeros)\s+)?", q)
    if not inicio:
        return None
    valor = r"(?:\d+|" + "|".join(ORDINALES) + r")(?!\w)"
    cola = q[inicio.end():]
    primero = re.match(valor, cola)
    if not primero:
        return None

    def numero(texto):
        return int(texto) if texto.isdigit() else ORDINALES[texto]

    numeros = [numero(primero[0])]
    fin = primero.end()
    while True:
        siguiente = re.match(r"\s*(,|;|y|e|al|a|hasta|[-–])\s*(" + valor + r")", cola[fin:])
        if not siguiente:
            break
        nuevo = numero(siguiente[2])
        if siguiente[1] in ("al", "a", "hasta", "-", "–"):
            if nuevo < numeros[-1] or nuevo - numeros[-1] > 1000:
                raise ValueError("Rango de artículos inválido. Ejemplo: artículos 3 al 4.")
            numeros.extend(range(numeros[-1] + 1, nuevo + 1))
        else:
            numeros.append(nuevo)
        fin += siguiente.end()
    if re.match(r"\.\d", cola[fin:]):
        # Una subsección (3.1) debe interpretarse con el modelo, no como artículo 3.
        return None
    return list(dict.fromkeys(numeros))


def transcribir_articulos(pregunta, documento):
    """Vía rápida conservadora: artículos íntegros y delimitados en una misma página.

    Si un artículo continúa en otra página o hay una cabecera desconocida, la
    lectura completa con el modelo se encarga de la consulta.
    """
    q = normalizar(pregunta)
    solicitados = articulos_solicitados(pregunta)
    dice = solicitados is not None and re.search(r"\bque\s+(?:dicen?|establece[n]?|dispone[n]?|indica[n]?)\b", q)
    if not re.search(r"\barticulos?\b", q) or not (dice or re.search(
            r"\b(menciona\w*|enumera\w*|lista\w*|muestra\w*|transcrib\w*|dime|cuales)\b", q)):
        return None
    if re.search(r"\b(explic\w*|resum\w*|sobre|refer\w*|primeros|ultimos|"
                 r"compara\w*|difer\w*|excepto|exclu\w*|no)\b", q):
        return None
    if not dice and re.search(r"\bque\b", q):
        return None
    if solicitados is None and (re.search(r"\bsolo\b|\barticulo\b", q) or
                                re.search(r"\barticulos?\s+\d+\.\d", q)):
        return None
    conteo = contar_articulos("Cuantos articulos tiene", documento)
    if not conteo:
        return None
    fuentes = conteo[1]
    if [p.numero for p in fuentes] != list(range(fuentes[0].numero, fuentes[-1].numero + 1)):
        return None
    primera = documento.paginas[0].texto.splitlines()
    articulos = []
    for indice, p in enumerate(fuentes, 1):
        matches = list(PATRON_ARTICULO.finditer(p.texto))
        if indice > 1:
            lineas = p.texto[:matches[0].start()].strip().splitlines()
            if lineas != primera[:len(lineas)]:
                return None
        for j, m in enumerate(matches):
            if j + 1 < len(matches):
                fin = matches[j + 1].start()
            else:
                if indice == len(fuentes) and not re.search(
                        r"(?im)^REG[IÍ]STRESE[,\s]", p.texto[m.end():]):
                    return None
                cierre = re.search(r"(?im)^(?:Oficina de Secretar[ií]a General\b|"
                                   r"REG[IÍ]STRESE[,\s])", p.texto[m.end():])
                if not cierre:
                    return None
                fin = m.end() + cierre.start()
            articulo = p.texto[m.start():fin].strip()
            if len(articulo) < len(m[0]) + 5:
                return None
            numero = int(m[1]) if m[1].isdigit() else ORDINALES[normalizar(m[1])]
            articulos.append((numero, articulo, p))
    total = len(articulos)
    if solicitados is not None:
        presentes = {n for n, _, _ in articulos}
        faltantes = [n for n in solicitados if n not in presentes]
        if faltantes:
            return (f"No se encontraron los artículos {', '.join(map(str, faltantes))}. "
                    f"La resolución contiene {total} artículos, numerados del 1 al {total}."), fuentes
        indice_articulos = {n: (n, texto, p) for n, texto, p in articulos}
        articulos = [indice_articulos[n] for n in solicitados]
    fuentes = list({p.clave: p for _, _, p in articulos}.values())
    citas = {p.clave: i for i, p in enumerate(fuentes, 1)}
    cabecera = ("Artículos solicitados. Transcripción del texto extraído:" if solicitados is not None else
                f"El texto contiene {total} artículos. Transcripción del texto extraído:")
    return (cabecera + "\n\n" + "\n\n".join(
        f"{texto} [{citas[p.clave]}]" for _, texto, p in articulos)), fuentes
