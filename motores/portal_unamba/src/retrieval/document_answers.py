"""Evidencia de fichas institucionales, sin catálogos ni nombres prefijados."""
import re
from urllib.parse import urlsplit, unquote

from motores.portal_unamba.src.retrieval.bm25 import normalize


def _payment_label_words(label):
    ignored = {'para', 'por', 'del', 'las', 'los', 'con', 'sin', 'una', 'uno', 'de'}
    return [word for word in re.findall(r'[a-z]{4,}', normalize(label)) if word not in ignored]


def _matches_payment_label(label, query):
    """Compara el concepto aun si el documento contiene acentos mal extraídos."""
    if normalize(label) in query:
        return True
    label_words = _payment_label_words(label)
    query_words = set(re.findall(r'[a-z]{4,}', query))
    if not label_words:
        return False
    matches = sum(any(word == candidate or word.startswith(candidate) or candidate.startswith(word)
                      for candidate in query_words)
                  for word in label_words)
    # La coincidencia flexible se usa solo para reparar acentos defectuosos;
    # exigir todos los términos evita confundir dos constancias distintas.
    return matches == len(label_words)


def payment_code_answer(question, nodes):
    """Extrae pares código-concepto sin delegar una lista posicional al modelo."""
    query = normalize(question)
    if not re.search(r'\b(codigos?|pago|precio|tasa|concepto|constancia|carnet|carne|maestria|tesis)\b', query):
        return None
    pairs = {}
    for number, item in enumerate(nodes, 1):
        source = urlsplit(str(item.node.metadata.get('source_url', '')))
        if source.path.rstrip('/') != '/pagos-virtuales':
            continue
        for code, label in re.findall(r'^\s*(\d{5})\s*\n+\s*([^\n]+)', item.node.text, re.M):
            pairs.setdefault(code, (label.strip(), number))
    if not pairs:
        return None
    asked_codes = list(dict.fromkeys(re.findall(r'\b\d{5}\b', query)))
    if asked_codes:
        found = [(code, *pairs[code]) for code in asked_codes if code in pairs]
        if not found:
            return None
        lines = [f'- {code}: {label} [{number}]' for code, label, number in found]
        if re.search(r'\b(precio|soles|importe|costo)\b', query):
            return 'El valor indicado es un código de pago, no un precio en soles.\n\n' + '\n'.join(lines)
        return '\n'.join(lines)
    # En un seguimiento como "¿Y la de matrícula?", la pregunta anterior
    # aporta la familia (p. ej., "constancia") y la actual aporta el
    # diferenciador. No se deben exigir a la nueva opción las palabras de la
    # opción anterior ("estudios"), porque eso la volvería ambigua.
    current, marker, prior = query.partition('tema anterior')
    if marker:
        current_words = set(re.findall(r'[a-z]{4,}', current))
        prior_words = set(re.findall(r'[a-z]{4,}', prior))
        families = {'constancia', 'certificado', 'carnet', 'carne', 'tesis', 'maestria'}
        inherited_families = prior_words & families
        follow_up = []
        for code, (label, number) in pairs.items():
            label_words = set(_payment_label_words(label))
            if inherited_families <= label_words and current_words & label_words:
                follow_up.append((code, label, number))
        if follow_up:
            specificity = max(len(current_words & set(_payment_label_words(label)))
                              for _, label, _ in follow_up)
            follow_up = [entry for entry in follow_up
                         if len(current_words & set(_payment_label_words(entry[1]))) == specificity]
        if len(follow_up) == 1:
            code, label, number = follow_up[0]
            return f'El código de pago para «{label}» es {code}. [{number}]'
    matches = [(code, label, number) for code, (label, number) in pairs.items()
               if _matches_payment_label(label, query)]
    if matches:
        # "Curso de actualización" es parte de "Inscripción curso de
        # actualización". Cuando hay una coincidencia más específica, usarla.
        specificity = max(len(_payment_label_words(label)) for _, label, _ in matches)
        matches = [match for match in matches if len(_payment_label_words(match[1])) == specificity]
    if len(matches) == 1:
        code, label, number = matches[0]
        return f'El código de pago para «{label}» es {code}. [{number}]'
    return None


def service_entry_answer(question, nodes):
    """Responde requisitos de ingreso solo cuando la fuente los enuncia literalmente."""
    query = normalize(question)
    asks_requirement = bool(re.search(
        r'\b(documento|requisito|carnet|carne|dni|identificacion)\b.*\b('
        r'llevo|necesito|presento|ingresar|entrar|acceder)\b|\b(entrar|ingresar)\b.*\b(comedor|servicio)\b',
        query,
    ))
    if not asks_requirement or 'comedor' not in query:
        return None
    for number, item in enumerate(nodes, 1):
        source = urlsplit(str(item.node.metadata.get('source_url', '')))
        if source.path.rstrip('/') != '/comedor-universitario':
            continue
        match = re.search(r'(?:ingreso|entrar)[^\n.]{0,80}\bcon\s+([^\n.]+)', item.node.text, re.I)
        if match:
            return f'Para ingresar al comedor se exige {match.group(1).strip()}. [{number}]'
    return None


def operational_service_answer(question, nodes):
    """Respuestas literales para horarios, rutas y pasos operativos del portal."""
    query = normalize(question)
    by_path = {}
    for number, item in enumerate(nodes, 1):
        path = urlsplit(str(item.node.metadata.get('source_url', ''))).path.rstrip('/')
        by_path.setdefault(path, []).append((number, item.node.text))

    bus = by_path.get('/bus-universitario', [])
    if bus and re.search(r'\b(bus|paraderos?|circunvalacion|salida|retorno)\b', query):
        number, text = bus[0]
        if re.search(r'\b(paraderos?|ruta de salida)\b', query):
            block = re.search(r'### Ruta de Salida\s*(.*?)(?=### Ruta de Retorno|\Z)', text, re.S | re.I)
            if block:
                stops = []
                for value in re.findall(r'^\s*([^\n]+)$', block.group(1), re.M):
                    value = value.strip()
                    if value.isdigit() or not value:
                        continue
                    value = re.sub(r'(?:Origen|Paradero(?: Intermedio)?|Destino Final Principal)', '', value, flags=re.I)
                    value = re.sub(r'\s{2,}', ' ', value).strip(' <')
                    if value.lower().startswith(('salidas desde', 'sentido ')) or value in {'↓', '↑'}:
                        continue
                    if value and value not in stops:
                        stops.append(value)
                if stops:
                    return 'Paraderos de salida del bus: ' + '; '.join(stops) + f'. [{number}]'
        if re.search(r'\b(noche|nocturn)\b', query):
            match = re.search(r'\|\s*NOCHE\s*\|\s*([^|]+)\|\s*([^|]+)\|', text, re.I)
            if match:
                return f'En el turno noche, el bus sale a las {match.group(1).strip()} y retorna a las {match.group(2).strip()}. [{number}]'
        if re.search(r'\b(manana|mañana)\b', query):
            match = re.search(r'\|\s*MAÑANA\s*\|\s*([^|]+)\|\s*([^|]+)\|', text, re.I)
            if match:
                return f'En la mañana, las salidas son {match.group(1).strip()} y los retornos {match.group(2).strip()}. [{number}]'

    medical = by_path.get('/servicios-medicos', [])
    if medical and re.search(r'\b(psicologia|psicologo)\b', query):
        for number, text in medical:
            match = re.search(r'\|\s*Psicolog[ií]a\s*\|\s*([^|]+)\|\s*([^|]+)\|', text, re.I)
            if match:
                return f'Psicología atiende {match.group(1).strip()}, de {match.group(2).strip()}. [{number}]'

    if medical and '/comedor-universitario' in by_path and re.search(r'\b(compara|comparacion|diferencia)\b', query):
        comedor_number, comedor_text = by_path['/comedor-universitario'][0]
        medical_number, medical_text = medical[0]
        requirement = re.search(r'(?:ingreso|entrar)[^\n.]{0,80}\bcon\s+([^\n.]+)', comedor_text, re.I)
        identity = re.search(r'Presentar\s+([^\n.]+)', medical_text, re.I)
        triage = re.search(r'(Pasar obligatoriamente por[^\n.]+)', medical_text, re.I)
        care = re.search(r'(Atenci[oó]n directa en[^\n.]+)', medical_text, re.I)
        if requirement and identity and triage and care:
            return (f'Comedor: se exige puntualidad e ingreso con {requirement.group(1).strip()}. [{comedor_number}]\n\n'
                    f'Centro médico: {identity.group(1).strip()}; Triaje: {triage.group(1).strip()}; {care.group(1).strip()}. [{medical_number}]')

    history = by_path.get('/nuestra-universidad', [])
    if history and '2015' in query and re.search(r'\b(creo|creada|creacion|paso|ocurrio)\b', query):
        for number, text in history:
            if (re.search(r'creada en el a[nñ]o 2000', text, re.I)
                    and '007-2015-CEUTA UNAMBA' in text
                    and re.search(r'03 de julio del 2015', text, re.I)):
                return ('La UNAMBA fue creada en 2000. En 2015 se reconoció a los integrantes de su Asamblea Estatutaria '
                        'mediante la Resolución Nº 007-2015-CEUTA UNAMBA, el 03 de julio. '
                        f'[{number}]')
    return None


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
    # Las preguntas reales terminan normalmente en "?"; separarlo evita que
    # "Administración?" deje de coincidir con el nombre de la ficha.
    query = set(re.findall(r'[a-z]+', normalize(question)))
    return bool(words & query)


def priority_records(question, records):
    """Reserva bloques pertinentes de la misma ficha; no sigue enlaces."""
    query = normalize(question)
    result, seen_roles, location_paths = [], set(), set()
    location = requested_school_location(question)
    vocational = vocational_terms(question)
    payment_terms = bool(re.search(r'\b(codigos?|pago|precio|tasa|concepto|constancia|carnet|carne|maestria|tesis)\b', query))
    payment_codes = set(re.findall(r'\b\d{5}\b', query))
    asks_comedor_requirement = (
        'comedor' in query
        and bool(re.search(r'\b(documento|requisito|carnet|carne|dni|entrar|ingresar)\b', query))
    )
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
                (re.search(r'\b(dura|duracion|semestres)\b', query) and 'duracion' in clean)
                or ('grado academico' in query and 'grado academico' in clean)
                or ('titulo profesional' in query and 'titulo profesional' in clean)
                or (re.search(r'\bdirector(?:a)?\b', query) and re.search(r'\bdirector(?:a)?\b', clean))
                or (re.search(r'\b(sedes|filiales|locales)\b', query) and ('locales / sedes' in clean or re.search(r'^[-*]\s*(?:sede|filial)', clean, re.M)))
                or ('laboratorio' in query and re.search(r'^#{1,6}\s+.*laboratorio', clean, re.M)))
        if 'bienvenida' in query and 'rector' in query:
            relevant = path == '/bienvenida-del-rector' and len(text) > 250
        if re.search(r'\b(numero|telefono)\b', query) and 'unamba' in query:
            relevant = 'central telefonica' in clean
        if re.search(r'\b(acoso|denunciar|denuncia)\b', query):
            relevant = path == '/servicios-universitarios' and 'defensor' in clean
        if payment_terms and path == '/pagos-virtuales':
            pairs = re.findall(r'^\s*(\d{5})\s*\n+\s*([^\n]+)', text, re.M)
            relevant = any(code in payment_codes or _matches_payment_label(label, query) for code, label in pairs)
        if asks_comedor_requirement and path == '/comedor-universitario' and re.search(
                r'\b(carne|carnet|dni)\b', clean):
            relevant = True
        if path == '/bus-universitario' and 'bus' in query and (
                ('paradero' in query and 'ruta de salida' in clean)
                or (re.search(r'\b(noche|nocturn)\b', query) and 'noche' in clean)
                or (re.search(r'\b(manana|mañana)\b', query) and 'manana' in clean)):
            relevant = True
        if path == '/servicios-medicos' and (
                ('psicologia' in query and 'psicologia' in clean)
                or (re.search(r'\b(compara|comparacion|diferencia)\b', query) and ('triaje' in clean or 'carnet universitario' in clean))):
            relevant = True
        if path == '/nuestra-universidad' and '2015' in query and ('asamblea estatutaria' in clean or 'creada en el ano 2000' in clean):
            relevant = True
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
    duration = bool(re.search(r'\b(cuanto dura|duracion|semestres)\b', query))
    degree = 'grado academico' in query
    title = 'titulo profesional' in query
    director = bool(re.search(r'\bdirector(?:a)?\b', query))
    sites = bool(re.search(r'\b(sede|sedes|filial|filiales|local|locales)\b', query))
    laboratories = bool(re.search(r'\blaboratorios?\b', query))
    if sum((duration, degree, title, director, sites, laboratories)) != 1:
        return None
    entries, scopes = {}, set()
    for number, item in enumerate(nodes, 1):
        if not school_scope(question, item.node.metadata):
            continue
        scopes.add(urlsplit(str(item.node.metadata.get('source_url', ''))).path.rstrip('/'))
        text = item.node.text
        if duration:
            values = re.findall(r'Duraci[oó]n\s*:?\s*(\d+\s+semestres(?:\s+acad[eé]micos?)?)\b', text, re.I)
        elif degree:
            values = re.findall(r'Grado\s+Acad[eé]mico\s*:?\s*([^\n]+)', text, re.I)
        elif title:
            values = re.findall(r'T[ií]tulo\s+Profesional\s*:?\s*([^\n]+)', text, re.I)
        elif director:
            values = [f'Dirección ({site.strip()}): {name.strip()}' for site, name in re.findall(
                r'Director(?:a)?\s*\(([^)\n]+)\)\s*:?\s*([^\n]+)', text, re.I)]
            values += [f'Dirección de filial: {name.strip()}' for name in re.findall(
                r'^Director(?:a)?\s+Filial\s*:?\s*([^\n]+)', text, re.I | re.M)]
            values += [f'Dirección: {name.strip()}' for name in re.findall(
                r'^Director(?:a)?(?!\s*\(|\s+Filial)\s*:?\s*([^\n]+)', text, re.I | re.M)]
        elif sites:
            values = re.findall(r'^\s*[-*]\s*((?:Sede|Filial)[^\n]+)', text, re.I | re.M)
        else:
            values = re.findall(r'^#{1,6}\s+([^\n]*Laboratorio[^\n]*)', text, re.I | re.M)
        for value in values:
            entries.setdefault(value.strip(), number)
    if len(scopes) != 1 or not entries or ((duration or degree or title) and len(entries) != 1):
        return None
    prefix = ('Duración indicada en la ficha: ' if duration else
              'Grado académico indicado en la ficha: ' if degree else
              'Título profesional indicado en la ficha: ' if title else
              'Según la ficha de la escuela:\n\n')
    exact_value = duration or degree or title
    return prefix + '\n'.join(f'{"- " if not exact_value else ""}{value} [{number}]' for value, number in entries.items())


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
