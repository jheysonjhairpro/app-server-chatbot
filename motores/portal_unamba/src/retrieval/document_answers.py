"""Evidencia de fichas institucionales, sin catálogos ni nombres prefijados."""
import re
from urllib.parse import urlsplit, unquote

from motores.portal_unamba.src.retrieval.bm25 import normalize


def asks_professional_catalog(question):
    return bool(re.fullmatch(
        r'(?:que|cuales|cuales son las) (?:escuelas profesionales|carreras(?: profesionales)?) '
        r'(?:(?:tiene|ofrece|hay en) )?(?:la )?unamba(?: (?:vro|bro))?',
        normalize(question).strip(' ¿?!.')))


def requested_school_location(question):
    match = re.fullmatch(r'(?:que|cuales) (?:escuelas profesionales|carreras(?: profesionales)?) '
                         r'(?:hay|existen|ofrece la unamba) en (?:la )?(?:(?:filial|sede) (?:de )?)?([a-z]+)',
                         normalize(question).strip(' ¿?!.'))
    return match[1] if match else None


def vocational_terms(question):
    query = normalize(question)
    if not re.search(r'\b(estudiar|carrera)\b', query) or not re.search(
            r'me gusta|interes|(?:que|cual|podria|puedo|deberia).{0,40}estudiar', query):
        return None
    if re.search(r'\b(sembrar|tomate|papa|cultivar)\b', query):
        return r'\b(?:agricolas?|agropecuario|agropecuaria|cultivos?)\b'
    if re.search(r'\b(minerales|mineria)\b', query):
        return r'\b(?:minerales|minera|mineria)\b'
    if 'medio ambiente' in query:
        return r'\b(?:ambiental|ambiente|sostenibles?|sostenibilidad)\b'
    return None


def professional_catalog_answer(question, nodes):
    if not asks_professional_catalog(question):
        return None
    for number, item in enumerate(nodes, 1):
        if urlsplit(str(item.node.metadata.get('source_url', ''))).path.rstrip('/') != '/escuelas-profesionales':
            continue
        labels = []
        for line in item.node.text.splitlines():
            parts = line.removeprefix('- ').split(' > ')
            if line.startswith('- ') and len(parts) >= 2 and normalize(parts[-2]).startswith('facultad de '):
                label = parts[-1].strip(' *#.:')
                if label and normalize(label) not in {'buscador', 'facultades', 'contacto', 'menu', 'enlaces de interes'} and label not in labels:
                    labels.append(label)
        if labels:
            return 'La página de escuelas profesionales enumera:\n\n' + '\n'.join(
                f'- {label} [{number}]' for label in labels)
    return None


def school_scope(question, metadata):
    source = urlsplit(str(metadata.get('source_url', '')))
    if source.hostname not in {'www.unamba.edu.pe', 'unamba.edu.pe'}:
        return False
    path = unquote(source.path)
    if not path.startswith('/escuela-profesional/'):
        return False
    words = set(normalize(path.split('/')[2].replace('-', ' ')).split())
    words -= {'ingenieria', 'profesional', 'escuela', 'de', 'y', 'en'}
    query = set(normalize(question).split())
    return bool(words & query)


def priority_records(question, records):
    """Reserva bloques pertinentes de la misma ficha; no sigue enlaces."""
    query = normalize(question)
    result, seen_roles, location_paths = [], set(), set()
    location = requested_school_location(question)
    vocational = vocational_terms(question)
    vocational_paths = set()
    for cid, (text, metadata) in records.items():
        clean = normalize(text)
        path = urlsplit(str(metadata.get('source_url', ''))).path.rstrip('/')
        relevant = False
        if location and path.startswith('/escuela-profesional/') and re.search(
                r'^[-*]\s+(?:sede|filial)[^\n]*\b' + re.escape(location) + r'\b', clean, re.M):
            relevant = True
            location_paths.add(path)
        if school_scope(question, metadata):
            relevant = bool(
                (re.search(r'\b(dura|duracion|director|directora)\b', query) and 'duracion' in clean)
                or ('laboratorio' in query and re.search(r'^#{1,6}\s+.*laboratorio', clean, re.M)))
        if 'bienvenida' in query and 'rector' in query:
            relevant = path == '/bienvenida-del-rector' and len(text) > 250
        if re.search(r'\b(numero|telefono)\b', query) and 'unamba' in query:
            relevant = 'central telefonica' in clean
        if re.search(r'\b(acoso|denunciar|denuncia)\b', query):
            relevant = path == '/servicios-universitarios' and 'defensor' in clean
        if vocational and path.startswith('/escuela-profesional/'):
            relevant = bool(re.search(vocational, clean))
            if relevant:
                vocational_paths.add(path)
        if re.search(r'\b(autoridades|decanos)\b', query):
            pairs = authority_pairs(text) if path.startswith('/autoridades/') else []
            if 'decanos' in query:
                pairs = [pair for pair in pairs if normalize(pair[1]).startswith(('decano', 'decana'))]
            keys = {(normalize(name), normalize(role)) for name, role in pairs}
            relevant = bool(keys - seen_roles)
            seen_roles |= keys
        if relevant:
            result.append(cid)
    # El nombre literal de la escuela puede estar en otro bloque de la ficha.
    names = {}
    for cid, (text, metadata) in records.items():
        path = urlsplit(str(metadata.get('source_url', ''))).path.rstrip('/')
        if path in location_paths | vocational_paths:
            slug = path.split('/')[-1].replace('-', ' ')
            if any(normalize(label).strip() == slug for label in re.findall(r'^#{1,6}\s+([^\n]+)', text, re.M)):
                if cid not in result:
                    names.setdefault(path, cid)
    ordered, seen_paths = [], set()
    for cid in result:
        path = urlsplit(str(records[cid][1].get('source_url', ''))).path.rstrip('/')
        if path not in seen_paths:
            seen_paths.add(path)
            if path in names:
                ordered.append(names[path])
            ordered.append(cid)
    return list(dict.fromkeys(ordered + result))


def vocational_answer(question, nodes):
    terms = vocational_terms(question)
    if not terms:
        return None
    labels, excerpts = {}, {}
    for number, item in enumerate(nodes, 1):
        source = urlsplit(str(item.node.metadata.get('source_url', '')))
        if source.hostname not in {'www.unamba.edu.pe', 'unamba.edu.pe'} or not source.path.startswith('/escuela-profesional/'):
            continue
        path = source.path.rstrip('/')
        slug = path.split('/')[-1].replace('-', ' ')
        for label in re.findall(r'^#{1,6}\s+([^\n]+)', item.node.text, re.M):
            if normalize(label).strip() == slug:
                labels.setdefault(path, (label.strip(), number))
        for line in item.node.text.splitlines():
            line = line.strip().removeprefix('- ')
            if not line or line.startswith(('#', '!', '[', 'url:')):
                continue
            # Mostrar una descripción literal, sin atribuir cultivos específicos
            # ni habilidades que solo aparecen en la pregunta del usuario.
            for sentence in re.split(r'(?<=[.!?])\s+', line):
                if re.search(terms, normalize(sentence)) and 30 <= len(sentence) <= 600:
                    excerpts.setdefault(path, (sentence, number))
                    break
    lines = []
    for path, (excerpt, number) in excerpts.items():
        if path in labels:
            label, label_number = labels[path]
            lines.append(f'- **{label}**: su ficha indica «{excerpt}» [{label_number}], [{number}]')
    if not lines:
        return None
    return ('Por el interés que mencionas, puedes explorar estas opciones documentadas:\n\n' + '\n'.join(lines)
            + '\n\nEs una orientación basada en los perfiles recuperados, no una evaluación vocacional ni una lista completa de opciones.')


def school_location_answer(question, nodes):
    location = requested_school_location(question)
    if not location:
        return None
    labels, sites = {}, {}
    for number, item in enumerate(nodes, 1):
        source = urlsplit(str(item.node.metadata.get('source_url', '')))
        if source.hostname not in {'www.unamba.edu.pe', 'unamba.edu.pe'} or not source.path.startswith('/escuela-profesional/'):
            continue
        path = source.path.rstrip('/')
        slug = path.split('/')[-1].replace('-', ' ')
        for label in re.findall(r'^#{1,6}\s+([^\n]+)', item.node.text, re.M):
            if normalize(label).strip() == slug:
                labels.setdefault(path, (label.strip(), number))
        if re.search(r'^[-*]\s+(?:sede|filial)[^\n]*\b' + re.escape(location) + r'\b', normalize(item.node.text), re.M):
            sites.setdefault(path, number)
    lines = [f'- {labels[path][0]} [{labels[path][1]}], [{number}]' for path, number in sites.items() if path in labels]
    if not lines:
        return None
    return ('Las fichas de estas escuelas mencionan la ubicación consultada:\n\n' + '\n'.join(lines)
            + '\n\nLa relación corresponde a las fichas recuperadas y puede ser parcial.')


def school_fact_answer(question, nodes):
    query = normalize(question).strip(' ¿?!.')
    school = r'(?:la )?(?:(?:carrera|escuela)(?: profesional)? de )?'
    duration = re.fullmatch(r'(?:cuanto dura|cual es la duracion de|duracion de) ' + school + r'(.+)', query)
    director = re.fullmatch(r'(?:(?:quien es|dime quien es) (?:el |la )?)?(?:director|directora) de ' + school + r'(.+)', query)
    laboratories = re.fullmatch(r'(?:que laboratorios (?:tiene|hay en)|laboratorios de) ' + school + r'(.+)', query)
    if not (duration or director or laboratories):
        return None
    entries, scopes = {}, set()
    for number, item in enumerate(nodes, 1):
        if not school_scope(question, item.node.metadata):
            continue
        # No responder una variante restringida (sede, año, modalidad) con la
        # ficha general. Todas las palabras del programa deben pertenecer a ella.
        program = (duration or director or laboratories)[1]
        slug = urlsplit(str(item.node.metadata.get('source_url', ''))).path.split('/')[2]
        if not (set(program.split()) - {'de', 'y', 'en'}) <= set(normalize(slug.replace('-', ' ')).split()):
            continue
        scopes.add(urlsplit(str(item.node.metadata.get('source_url', ''))).path.rstrip('/'))
        text = item.node.text
        if duration:
            values = re.findall(r'Duraci[oó]n\s*:?\s*(\d+\s+semestres)\b', text, re.I)
        elif director:
            values = [f'Dirección ({site.strip()}): {name.strip()}' for site, name in re.findall(
                r'Director(?:a)?\s*\(([^)\n]+)\)\s*:?\s*([^\n]+)', text, re.I)]
            values += [f'Dirección de filial: {name.strip()}' for name in re.findall(
                r'^Director(?:a)?\s+Filial\s*:?\s*([^\n]+)', text, re.I | re.M)]
        else:
            values = re.findall(r'^#{1,6}\s+([^\n]*Laboratorio[^\n]*)', text, re.I | re.M)
        for value in values:
            entries.setdefault(value.strip(), number)
    if len(scopes) != 1 or not entries or (duration and len(entries) != 1):
        return None
    prefix = 'Duración indicada en la ficha: ' if duration else 'Según la ficha de la escuela:\n\n'
    return prefix + '\n'.join(f'{"- " if not duration else ""}{value} [{number}]' for value, number in entries.items())


def authority_pairs(text):
    """Solo un nombre y su cargo adyacente; nunca el alt de una imagen."""
    return re.findall(r'^#{1,6}\s+((?:Dr\.?|Dra\.?|Mg\.?|Mag\.?)\s+[^\n]+)\n\s*'
                      r'/\s*((?:Rector|Vicerrector|Decano|Decana)[^/\n]*)\s*/', text, re.M | re.I)


def authority_roster_answer(question, nodes):
    query = normalize(question).strip(' ¿?!.')
    if not re.fullmatch(r'(?:autoridades|decanos(?: de (?:la|las)?\s*facultades)?) (?:de (?:la )?)?unamba', query):
        return None
    entries = {}
    for number, item in enumerate(nodes, 1):
        source = urlsplit(str(item.node.metadata.get('source_url', '')))
        if source.hostname not in {'www.unamba.edu.pe', 'unamba.edu.pe'} or not source.path.startswith('/autoridades/'):
            continue
        for name, role in authority_pairs(item.node.text):
            if 'decanos' in query and not normalize(role).startswith(('decano', 'decana')):
                continue
            entries.setdefault((name.strip(), role.strip()), number)
    if not entries:
        return None
    return ('La página institucional de autoridades identifica a:\n\n' + '\n'.join(
        f'- {role}: {name} [{number}]' for (name, role), number in entries.items())
        + '\n\nLa relación corresponde a los fragmentos recuperados y puede ser parcial; no confirma cambios posteriores a lo documentado.')


def institutional_contact_answer(question, nodes):
    query = normalize(question).strip(' ¿?!.')
    phone = bool(re.fullmatch(r'(?:(?:cual es el|dame el) )?(?:numero(?: telefonico)?|telefono) de (?:la )?unamba', query))
    referral = bool(re.search(r'\b(acoso|denunciar|denuncia)\b', query))
    if not (phone or referral):
        return None
    entries = {}
    for number, item in enumerate(nodes, 1):
        source = urlsplit(str(item.node.metadata.get('source_url', '')))
        if source.hostname not in {'www.unamba.edu.pe', 'unamba.edu.pe'}:
            continue
        if phone:
            for value in re.findall(r'Central Telef[oó]nica\s*[:* ]*\s*(\(\d{2,4}\)\s*\d[\d -]{4,12}\d)', item.node.text, re.I):
                entries.setdefault(value.strip(), number)
        elif source.path.rstrip('/') == '/servicios-universitarios':
            for label, url in re.findall(r'(?<!!)\[([^\]\n]+)\]\((https?://[^\s)]+)\)', item.node.text):
                if 'defensor' in normalize(label):
                    entries.setdefault((label, url), number)
    if not entries or (phone and len(entries) != 1):
        return None
    if phone:
        value, number = next(iter(entries.items()))
        return f'La central telefónica indicada por la UNAMBA es {value}. [{number}]'
    return ('El portal de servicios universitarios incluye esta referencia para orientación:\n\n' + '\n'.join(
        f'- [{label}]({url}) [{number}]' for (label, url), number in entries.items())
        + '\n\nLos documentos disponibles no detallan el procedimiento ni confirman un canal específico para presentar esa denuncia. No se ha leído la página de destino.')
