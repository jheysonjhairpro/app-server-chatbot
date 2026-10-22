"""Variantes de búsqueda; amplían vocabulario sin generar hechos ni respuestas."""

import re

from motores.portal_unamba.src.retrieval.bm25 import tokenize

INSTITUTION_TERMS = set(tokenize("UNAMBA Universidad Nacional Micaela Bastidas de Apurímac"))
SEARCH_TERMS = set(tokenize("lista listado información disponible ofrece ofrecidos tiene tener"))


def safe_queries(question: str, candidates: list[str]) -> list[str]:
    """Plan extractivo: no permite inventar entidades, niveles, fechas ni temas.

    Se admiten omisiones y cambios de orden, además de palabras de búsqueda
    neutras. La pregunta original siempre se ejecuta por separado.
    """
    original = content_terms(question) - SEARCH_TERMS
    allowed = set(tokenize(question)) | INSTITUTION_TERMS | SEARCH_TERMS
    numbers = {term for term in tokenize(question) if term.isdigit()}
    result = []
    for query in candidates:
        query = query.strip()
        terms = set(tokenize(query))
        if (3 <= len(query) <= 500 and terms <= allowed
                and original and terms & original and numbers <= terms):
            result.append(query)
    return list(dict.fromkeys(result))


def content_terms(question: str) -> set[str]:
    """El nombre de la universidad por sí solo no justifica recuperar cualquier página."""
    return set(tokenize(question)) - INSTITUTION_TERMS


def asks_for_list(question: str) -> bool:
    text = question.lower()
    return bool(re.search(r'\b(lista|listado|enumera|enumerar|cu[aá]les)\b', text)
                or re.search(r'^\s*[¿]?\s*(?:carreras|maestr[ií]as|mestrias|filiales|sedes)\b|\bcu[aá]ntas\s+(?:filiales|sedes)\b', text)
                or re.search(r'^\s*[¿]?\s*(autoridades|decanos|integrantes|miembros)\b', text)
                or re.search(r'\bqu[eé]\b.+\b(tiene|ofrece|hay|existen|dispone)\b', text))


def question_parts(question: str) -> list[str]:
    question = question.split(" (Tema anterior:", 1)[0]
    parts = re.split(r"[?;]+|\by\s+(?=(?:cu[aá]l|cu[aá]nt|c[oó]mo|d[oó]nde|qu[eé]|cu[aá]ndo))", question, flags=re.I)
    return [part.strip(" ¿¡.,") for part in parts if part.strip(" ¿¡.,")][:4]


def search_queries(question: str) -> list[str]:
    queries = [question]
    # Expansiones de vocabulario para recuperar evidencia, nunca respuestas.
    from motores.portal_unamba.src.retrieval.bm25 import normalize
    text = normalize(question)
    if re.fullmatch(r'numero (?:de (?:la )?)?unamba[ ?!.]*', text):
        queries.append(question + ' teléfono contacto')
    if re.search(r'\b(sembrar|tomate|papa|cultivar)\b', text):
        queries.append('producción agrícola agroecología agropecuaria perfil profesional')
    if re.search(r'\b(acoso|acos[oó]|denunciar|denuncia)\b', text):
        queries.append(question + ' defensoría universitaria')
    # Conserva siempre la consulta completa; separa subpreguntas explícitas.
    parts = question_parts(question)
    for index, part in enumerate(parts):
        if part and content_terms(part) and part != question.strip(" ¿¡?.,"):
            if index > 0:
                part = f"{part} ({parts[0]})"
            queries.append(part)
    return list(dict.fromkeys(queries))[:4]
