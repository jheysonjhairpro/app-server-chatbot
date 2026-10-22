"""Comparación de evidencia sin confundir URLs distintas con contenido nuevo."""

import re

from motores.portal_unamba.src.retrieval.bm25 import normalize
from motores.portal_unamba.src.retrieval.bm25 import tokenize
from motores.portal_unamba.src.retrieval.queries import content_terms, SEARCH_TERMS, INSTITUTION_TERMS


def heading_label(text):
    """Texto visible de un título, conservando su escritura original."""
    text = re.sub(r'!?\[([^\]]*)\]\([^\s)]*\)', r'\1', text)
    return ' '.join(text.split()).strip(' *#.:')


def authority_identity_answer(question, nodes):
    """Nombre y cargo literales de una página institucional de identificación."""
    from urllib.parse import urlsplit
    query = normalize(question).strip(' ¿?!.')
    match = re.fullmatch(r'(?:quien es|quienes son) (.+?)(?: de (?:la )?unamba)?', query)
    if not match:
        return None
    requested = [part.removeprefix('el ').strip() for part in match[1].split(' y ')]
    pages = {'rector': '/bienvenida-del-rector', 'vicerrector academico': '/vicerrectorado-academico',
             'vicerrector de investigacion': '/vicerrectorado-de-investigacion'}
    if not requested or any(role not in pages for role in requested):
        return None
    found = {role: {} for role in requested}
    for number, node in enumerate(nodes, 1):
        source = urlsplit(str(node.node.metadata.get('source_url', '')))
        if source.hostname not in {'www.unamba.edu.pe', 'unamba.edu.pe'}:
            continue
        # El nombre debe ser un encabezado; el cargo, una línea del mismo bloque.
        blocks = re.split(r'(?=^#{1,6}\s+)', node.node.text, flags=re.M)
        for block in blocks:
            heading = re.match(r'^#{1,6}\s+((?:Dr\.?|Dra\.?|Mg\.?)\s+[^\n]+)\n', block)
            if not heading:
                continue
            name = heading_label(heading[1])
            for role in requested:
                if source.path.rstrip('/') == pages[role] and re.search(r'^\s*' + re.escape(role) + r'\s*$', normalize(block), re.M):
                    found[role].setdefault(normalize(name), (name, number))
    if any(len(names) != 1 for names in found.values()):
        return None  # Falta identidad o hay una discrepancia: usar extracción con evidencia.
    return 'Según las páginas institucionales recuperadas:\n\n' + '\n'.join(
        f'- {role.capitalize().replace("academico", "académico").replace("investigacion", "investigación")}: {next(iter(found[role].values()))[0]} [{next(iter(found[role].values()))[1]}]'
        for role in requested)


def linked_masters_list(question, nodes):
    query = normalize(question).strip(' ¿?!.')
    if not re.fullmatch(r'(?:(?:que|cuales|cuales son las) )?(?:maestrias|mestrias)(?: de posgrado)?(?: ofrece| tiene| de)? (?:la )?unamba(?: (?:vro|bro))?', query):
        return None
    found = []
    for number, node in enumerate(nodes, 1):
        entries = []
        for label, url in re.findall(r'(?<!!)\[([^\]\n]+)\]\((https?://[^\s)]+)\)', node.node.text):
            if re.match(r'^maestria\s+en\b', normalize(heading_label(label))):
                entry = (heading_label(label), url)
                if entry not in entries:
                    entries.append(entry)
        if len(entries) >= 2:
            found.append((entries, number))
    if not found:
        return None
    entries, number = max(found, key=lambda pair: len(pair[0]))
    return ('El portal de UNAMBA incluye estas referencias a maestrías:\n\n' + '\n'.join(
        f'- [{label}]({url}) [{number}]' for label, url in entries)
        + '\n\nEsta lista corresponde a enlaces documentados del portal; no confirma una convocatoria vigente ni los detalles de las páginas externas.')


def branch_labels(text):
    labels = []
    for line in text.splitlines():
        match = re.fullmatch(r'(?:#{1,6}\s+.*?[-–]\s*|[-*]\s*|#{1,6}\s*)?Filial\s+([^\n]+)', line.strip(), re.I)
        if not match:
            continue
        label = re.sub(r'\s*\([^)]*\)', '', match[1]).strip(' .')
        if label and not re.search(r'[\[\]<>/:\d]', label):
            labels.append('Filial ' + label)
    return labels


def branch_reference_list(question, nodes):
    query = normalize(question).strip(' ¿?!.')
    if not re.fullmatch(r'(?:(?:cuantas|que|cuales) )?(?:sedes o filiales|filiales|sedes)(?: tiene| de)? (?:la )?unamba(?: y dime cuales? son)?[} ]*', query):
        return None
    found = {}
    for number, node in enumerate(nodes, 1):
        for label in branch_labels(node.node.text):
            found.setdefault(normalize(label), (label, number))
    # Una denominación corta puede ser la misma referencia que otra más extensa;
    # no convertir variantes de nombres en una cifra institucional.
    entries = [pair for key, pair in found.items() if not any(
        key != other and set(tokenize(key)) < set(tokenize(other)) for other in found)]
    if not entries:
        return None
    return ('No puedo confirmar un total institucional con estos fragmentos. '
            'Sí encontré estas referencias explícitas a filiales:\n\n' + '\n'.join(
                f'- {label} [{number}]' for label, number in entries)
            + '\n\nLa sede central no se cuenta como filial en esta relación. La lista puede ser parcial y los nombres pueden tener variantes.')


def faculty_career_list(question, nodes):
    """Lista literal de hijos de una facultad explícita, sin inferir atributos."""
    query = normalize(question).strip(' ¿?!.')
    # No aplicar a recomendaciones, fechas, requisitos ni otras restricciones.
    match = re.fullmatch(r'(?:que|cuales|cuales son las) (?:carreras|carreras profesionales) '
                         r'(?:tiene|ofrece|hay en|existen en) (?:la )?(facultad de .+)', query)
    if not match:
        return None
    scope = match[1]
    catalogs = []
    for number, node in enumerate(nodes, 1):
        if not node.node.node_id.startswith('outline:'):
            continue
        children = []
        for line in node.node.text.splitlines():
            if not line.startswith('- '):
                continue
            path = [heading_label(part) for part in line[2:].split(' > ')]
            for index, label in enumerate(path[:-1]):
                if normalize(label) == scope:
                    child = path[index + 1]
                    if child not in children:
                        children.append(child)
        if children and not any(child.isupper() for child in children):
            catalogs.append((children, number))
    # Si las páginas discrepan, dejar que el generador explique la discrepancia.
    if not catalogs or len({tuple(normalize(c) for c in children) for children, _ in catalogs}) != 1:
        return None
    children, number = catalogs[0]
    return 'Según los encabezados de la fuente consultada, se encuentran estas carreras:\n\n' + '\n'.join(
        f'- {child} [{number}]' for child in children)


def complete_heading_list(question, answer, nodes):
    """Completa enumeraciones simples ya reconocidas por el generador.

    Solo opera cuando todos los elementos generados son nombres literales de
    una misma jerarquía. No completa descripciones, preguntas con restricciones,
    listas numéricas limitadas ni listas mezcladas. Los nombres proceden del
    corpus, nunca de un catálogo por tema.
    """
    terms = content_terms(question) - SEARCH_TERMS
    if (len(terms) != 1 or re.search(r'\d', question)
            or re.search(r'\b(no|solo|solamente|excepto|sin)\b', normalize(question))):
        return None
    items = re.findall(r'^\s*(?:[-*•]|\d+[.)])\s+(.+)', answer, re.M)
    if len(items) == 1:
        return None
    names = {normalize(heading_label(re.sub(r'\[\d+\]', '', item))) for item in items}
    catalogs = []
    for number, item in enumerate(nodes, 1):
        if not item.node.node_id.startswith('outline:'):
            continue
        labels = []
        for line in item.node.text.splitlines():
            if not line.startswith('- '):
                continue
            parts = [heading_label(part) for part in line[2:].split(' > ')]
            for index, part in enumerate(parts):
                words = re.findall(r'[^\W\d_]+', part, re.UNICODE)
                if not words or set(tokenize(words[0])) != terms:
                    continue
                # Una categoría plural agrupa los títulos hijos; una entidad
                # singular con nombre propio se enumera en su propio nivel.
                if words[0].lower().endswith('s'):
                    label = parts[index + 1] if index + 1 < len(parts) else ''
                else:
                    label = part if len(words) > 1 else ''
                if label and label not in labels:
                    labels.append(label)
                break
        expected = {normalize(label) for label in labels}
        matches = bool(items) and names <= expected
        if not items:
            # También admite prosa que solo enumera nombres: cualquier dato
            # adicional (costos, fechas, funciones, negaciones) impide esta vía.
            remainder = visible_text(re.sub(r'\[\d+\]', '', answer))
            found = 0
            for label in sorted(expected, key=len, reverse=True):
                pattern = r'(?<!\w)' + re.escape(label) + r'(?!\w)'
                remainder, count = re.subn(pattern, '', remainder)
                found += bool(count)
            neutral = set(tokenize('según las fuentes consultadas la institución cuenta ofrece tiene siguientes información universidad'))
            allowed = neutral | INSTITUTION_TERMS | set(tokenize(question))
            matches = (found >= 2 and not re.search(r'\d|\b(no|sin|excepto)\b', remainder)
                       and set(tokenize(remainder)) <= allowed)
        if len(expected) >= 2 and matches:
            catalogs.append((labels, number))
    if not catalogs:
        return None
    labels, number = max(catalogs, key=lambda pair: len(pair[0]))
    return 'Según los encabezados de la fuente consultada:\n\n' + '\n'.join(
        f'- {label} [{number}]' for label in labels)


def visible_text(text):
    text = re.sub(r"!?\[([^\]]*)\]\([^\s)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    return " ".join(normalize(text).split())


def linked_service_list(question, nodes):
    """Enumera enlaces recuperados de la página pedida, sin leer sus destinos."""
    query = normalize(question).strip(' ¿?!.')
    match = re.fullmatch(r'que (servicios universitarios) (?:ofrece|tiene) (?:la )?unamba(?: (?:vro|bro))?', query)
    if not match:
        return None
    entries, seen = [], set()
    for number, item in enumerate(nodes, 1):
        # El alcance procede de la página, no de enlaces de navegación genéricos.
        from urllib.parse import urlsplit
        source = str(item.node.metadata.get('source_url', ''))
        if urlsplit(source).path.rstrip('/') != '/servicios-universitarios':
            continue
        for label, url in re.findall(r'(?<!!)\[([^\]\n]+)\]\((https?://[^\s)]+)\)', item.node.text):
            label = heading_label(label)
            if normalize(label) in {'home', 'inicio'} or url.rstrip('/') == source.rstrip('/'):
                continue
            key = (normalize(label), url)
            if label and key not in seen:
                seen.add(key)
                entries.append(f'- [{label}]({url}) [{number}]')
    if not entries:
        return None
    return ('El portal de servicios universitarios enlaza estas herramientas y servicios '
            '(lista de enlaces recuperados; puede ser parcial):\n\n' + '\n'.join(entries)
            + '\n\nLos enlaces identifican referencias del portal; no se ha leído el contenido de sus destinos externos.')


def shingles(text):
    words = visible_text(text).split()
    return {tuple(words[i:i + 5]) for i in range(len(words) - 4)}


def redundant(text, previous):
    normalized = visible_text(text)
    current = shingles(text)
    for old in previous:
        if normalized == visible_text(old):
            return True
        other = shingles(old)
        # Solo descartamos contenido contenido íntegramente en otro bloque.
        # Una pequeña diferencia puede ser un precio, fecha o nombre importante.
        if len(current) >= 20 and current <= other and normalized in visible_text(old):
            return True
    return False


def merge_neighbors(texts):
    """Retira el solapamiento literal que SentenceSplitter añade entre vecinos."""
    merged = ""
    for text in texts:
        if not merged:
            merged = text
            continue
        overlap = 0
        for size in range(min(len(merged), len(text), 2000), 19, -1):
            if merged.endswith(text[:size]):
                overlap = size
                break
        merged += text[overlap:] if overlap else "\n\n" + text
    return merged


def cite_exact_headings(answer, nodes):
    """Completa citas omitidas solo si el nombre coincide con un título real.

    No asigna todas las fuentes al final ni cita un texto por mera similitud.
    Las afirmaciones añadidas después del nombre requieren su propia cita.
    """
    headings = {}
    links_by_source = {}
    for number, item in enumerate(nodes, 1):
        meta = item.node.metadata
        links = re.findall(r'(?<!!)\[([^\]]+)\]\((https?://[^\s)]+)\)', item.node.text)
        links_by_source[number] = {(visible_text(label).strip(' *#.:').replace('*', ''), url) for label, url in links}
        labels = [str(meta.get(f'header_{level}', '')) for level in range(1, 7)]
        for line in item.node.text.splitlines():
            if line.startswith('- ') and ' > ' in line:
                labels.append(line.rsplit(' > ', 1)[-1])
            match = re.match(r'^\s{0,3}#{1,6}\s+(.+)', line)
            if match:
                labels.append(match[1])
        for label in labels:
            label = visible_text(label).strip(' *#.:')
            if label:
                headings.setdefault(label, number)
        for label, _ in links_by_source[number]:
            if label:
                headings.setdefault(label, number)
    result = []
    for line in answer.splitlines():
        match = re.match(r'^\s*(?:[-*•]|\d+[.)])\s+(.+)', line)
        if match and not re.search(r'\[\d+\]', line):
            label = visible_text(match[1]).strip(' *#.:')
            label = label.replace('*', '')
            explicit = re.findall(r'(?<!!)\[([^\]]+)\]\((https?://[^\s)]+)\)', match[1])
            number = headings.get(label)
            if explicit:
                pairs = {(visible_text(text).strip(' *#.:').replace('*', ''), url) for text, url in explicit}
                number = next((index for index, links in links_by_source.items()
                               if pairs <= links and label in {text for text, _ in pairs}), None)
            if number is None and not explicit and len(tokenize(label)) >= 2:
                # También admite un elemento copiado literalmente del cuerpo;
                # nunca basta la coincidencia de un nombre con datos añadidos.
                for index, item in enumerate(nodes, 1):
                    evidence = visible_text(item.node.text).replace('*', '').replace('_', '')
                    literal = label.replace('*', '').replace('_', '')
                    if re.search(r'(?<!\w)' + re.escape(literal) + r'(?!\w)', evidence):
                        number = index
                        break
            if number:
                line += f' [{number}]'
        result.append(line)
    return '\n'.join(result)
