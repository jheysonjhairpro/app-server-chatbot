"""Read-only integrity and coverage controls for indexed public HTML pages."""
from dataclasses import dataclass
import re


class IndiceWebIncompleto(ValueError):
    pass


@dataclass(frozen=True)
class PaginaWeb:
    url: str
    meta: dict
    fragmentos: tuple
    texto: str


def unir_fragmentos(fragmentos):
    resultado = []
    anteriores = []
    cuerpo_anterior = ''
    for texto in fragmentos:
        lineas = texto.splitlines()
        encabezados = []
        while lineas and re.match(r'^#{1,6}\s', lineas[0]):
            encabezados.append(lineas.pop(0))
        comunes = 0
        for a, b in zip(anteriores, encabezados):
            if a != b:
                break
            comunes += 1
        cuerpo = '\n'.join(lineas)
        if encabezados == anteriores:
            for cantidad in range(min(400, len(cuerpo_anterior), len(cuerpo)), 19, -1):
                if cuerpo_anterior.endswith(cuerpo[:cantidad]):
                    cuerpo = cuerpo[cantidad:].lstrip()
                    break
        resultado.extend(encabezados[comunes:])
        if cuerpo:
            resultado.append(cuerpo)
        anteriores = encabezados
        cuerpo_anterior = '\n'.join(lineas)
    return '\n'.join(resultado)


def reconstruir_pagina(url, registros):
    if not registros:
        raise IndiceWebIncompleto('No hay fragmentos de la pagina seleccionada.')
    try:
        metas = [m for _, m in registros]
        total = {int(m['chunks']) for m in metas}
        numeros = sorted(int(m['chunk']) for m in metas)
        for campo in ('page_hash', 'extractor_version', 'embedding_model', 'title', 'domain'):
            valores = {m.get(campo) for m in metas}
            if len(valores) != 1 or not next(iter(valores)):
                raise ValueError(campo)
        if total != {len(registros)} or numeros != list(range(1, len(registros) + 1)):
            raise ValueError('secuencia')
        if any(m.get('url') != url or not isinstance(t, str) or not t.strip() for t, m in registros):
            raise ValueError('contenido')
    except (KeyError, TypeError, ValueError) as exc:
        raise IndiceWebIncompleto('La pagina tiene fragmentos faltantes, duplicados o versiones mezcladas; debe reindexarse.') from exc
    ordenados = tuple(sorted(registros, key=lambda item: int(item[1]['chunk'])))
    return PaginaWeb(url, ordenados[0][1], ordenados, unir_fragmentos([t for t, _ in ordenados]))


def leer_pagina(collection, url):
    registros, offset = [], 0
    while True:
        datos = collection.get(where={'url': url}, include=['documents', 'metadatas'], limit=500, offset=offset)
        docs, metas, ids = datos.get('documents', []), datos.get('metadatas', []), datos.get('ids', [])
        if len(docs) != len(metas) or len(ids) != len(docs):
            raise IndiceWebIncompleto('Lectura incompleta del indice web.')
        registros.extend(zip(docs, metas))
        if len(ids) < 500:
            break
        offset += len(ids)
    return reconstruir_pagina(url, registros)


def ventanas_pagina(pagina, limite):
    """Every character is covered, including single lines larger than the budget."""
    if limite < 1:
        raise ValueError('El limite debe ser positivo.')
    texto = pagina.texto
    inicio = 0
    while inicio < len(texto):
        fin = min(inicio + limite, len(texto))
        if fin < len(texto):
            salto = texto.rfind('\n', inicio, fin)
            if salto >= inicio:
                fin = salto + 1
            else:
                fin_linea = texto.find('\n', inicio)
                linea = texto[inicio:fin_linea if fin_linea >= 0 else len(texto)]
                if 'http' in linea or ' | ' in linea:
                    raise ValueError('Una fila o enlace supera el contexto disponible; no se dividira un dato estructurado.')
                espacio = texto.rfind(' ', inicio, fin)
                if espacio > inicio:
                    fin = espacio + 1
        yield texto[inicio:fin]
        inicio = fin


def exige_revision_completa(pregunta):
    return bool(re.search(r'\b(todos|todas|total|cuantos|cuantas|enumera\w*|lista\w*|compara\w*|complet[oa])\b', pregunta))


@dataclass(frozen=True)
class AclaracionWeb:
    pregunta: str
    candidatos: tuple


def resolver_aclaracion(texto, pendiente, normalizar):
    q = normalizar(texto).strip(' .¿?')
    tokens = set(re.findall(r'\w+', q)) - {'la', 'el', 'de', 'del', 'ciclo', 'periodo', 'ano'}
    if not tokens or len(tokens) > 6:
        return []
    return [m for m in pendiente.candidatos if tokens <= set(re.findall(r'\w+', normalizar(m['title'])))]


def contacto_directo(consulta, textos, aliases, normalizar):
    """Exact field requests only; unknown qualifiers and conflicting values fall back."""
    campos = {'telefono': ('telefono', 'celular'), 'correo': ('correo', 'email', 'correo electronico'),
              'direccion': ('direccion',), 'horario': ('horario', 'horario de atencion')}
    q = normalizar(consulta)
    if not any(re.search(r'\b' + re.escape(normalizar(a)) + r'\b', q) for a in aliases):
        return None
    restantes = q
    for alias in sorted(aliases, key=len, reverse=True):
        restantes = re.sub(r'\b' + re.escape(normalizar(alias)) + r'\b', ' ', restantes)
    pedidos = []
    for campo, nombres in campos.items():
        for nombre in sorted(nombres, key=len, reverse=True):
            patron = r'\b' + nombre + r'\b'
            if re.search(patron, restantes):
                if campo not in pedidos:
                    pedidos.append(campo)
                restantes = re.sub(patron, ' ', restantes)
    permitidas = {'indica', 'dame', 'cual', 'cuales', 'es', 'son', 'el', 'la', 'los', 'las', 'de', 'del', 'y', 'unamba'}
    if not pedidos or set(re.findall(r'\w+', restantes)) - permitidas:
        return None
    datos = []
    for campo in pedidos:
        valores = {}
        for ref, contenido in textos.items():
            for linea in contenido.splitlines():
                etiqueta, separador, valor = linea.partition(':')
                if separador and normalizar(etiqueta.strip()) in campos[campo] and valor.strip():
                    clave = normalizar(valor.strip())
                    if campo == 'telefono':
                        clave = re.sub(r'\s+', '', clave)
                    valores.setdefault(clave, {'texto': linea.strip(), 'fuente': ref, 'evidencia': linea.strip()})
        if len(valores) != 1:
            return None
        datos.append(next(iter(valores.values())))
    return {'estado': 'respuesta', 'aclaracion': '', 'datos': datos}
