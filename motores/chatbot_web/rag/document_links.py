"""Respuestas directas con enlaces documentales del catálogo."""

from indexing.cleaner import folded


def document_answer(state, question, urls):
    import re

    q = folded(question)
    if not any(
        re.search(r"\b" + intent + r"\b", q)
        for intent in ["donde", "enlace", "link", "url"]
    ):
        return None
    stop = {
        "donde",
        "encuentro",
        "oficial",
        "pagina",
        "universidad",
        "unamba",
        "puedo",
        "enlace",
        "enlaces",
        "documento",
        "documentos",
        "encontrar",
        "obtener",
        "informacion",
        "publican",
        "publica",
    }
    terms = {t for t in re.findall(r"\w+", q) if len(t) >= 4 and t not in stop}
    if not terms:
        return None
    links = state.document_links(urls)
    matches = []
    seen = set()
    for link in links:
        from urllib.parse import urlsplit, unquote

        filename = unquote(urlsplit(link["url"]).path.rsplit("/", 1)[-1])
        words = set(re.findall(r"\w+", folded(link["nombre"] + " " + filename)))
        if terms & words and link["url"] not in seen:
            display = dict(link)
            if folded(display["nombre"]) in {
                "descargar",
                "descargar pdf",
                "ver",
                "download",
            }:
                display["nombre"] = filename
            matches.append(display)
            seen.add(link["url"])
    if not matches:
        return None
    answer = "Enlaces documentales publicados en las páginas oficiales:\n\n"
    answer += "\n".join(f"- [{l['nombre'][:150]}]({l['url']})" for l in matches[:3])
    answer += "\n\nSolo se indexó la referencia HTML; no se ha leído el contenido de estos documentos."
    return answer
