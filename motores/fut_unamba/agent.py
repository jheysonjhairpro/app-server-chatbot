"""Diálogo FUT: datos validados, asistencia de Qwen y PDF temporal."""
import re
import json
import unicodedata
from difflib import SequenceMatcher
import secrets
import threading
import time
from dataclasses import dataclass, field

import ollama

from configuracion import servidor as config
from motores.fut_unamba import catalogo
from motores.fut_unamba import funcionarios
from motores.fut_unamba import contexto_fut
from motores.fut_unamba.pdf import render

SESSION_TTL = 3600
DOWNLOAD_TTL = 900
LOCK = threading.RLock()
SESSIONS = {}
DOWNLOADS = {}

FIELDS = [
    ('resumen', '¿Qué solicitas exactamente? Escribe el resumen de tu pedido.'),
    ('destinatario', '¿A qué autoridad o área va dirigido? Puedes decirme solo el cargo o área y buscaré el nombre publicado por la universidad.'),
    ('cargo', '¿Qué cargo tiene esa persona? También puedo buscarlo si me dices el área.'),
    ('nombre', 'Escribe tus apellidos y nombres completos.'),
    ('escuela', '¿Cuál es tu Escuela Profesional? Si no corresponde, escribe «omitir».'),
    ('codigo', '¿Cuál es tu código de estudiante? Si no corresponde, escribe «omitir».'),
    ('dni', 'Escribe tu DNI de 8 dígitos. Se usará solo para completar este FUT.'),
    ('direccion', 'Escribe tu dirección de domicilio. Puedes escribir «omitir» si no deseas incluirla.'),
    ('distrito', '¿Cuál es tu distrito?'),
    ('provincia', '¿Cuál es tu provincia?'),
    ('departamento', '¿Cuál es tu departamento?'),
    ('celular', 'Escribe un teléfono o celular de contacto; también puedes escribir «omitir».'),
    ('correo', 'Escribe tu correo electrónico; también puedes escribir «omitir».'),
    ('fundamentacion', '¿Para qué necesitas este documento o trámite? Cuéntame el motivo con tus palabras; te ayudaré a convertirlo en una fundamentación clara.'),
    ('adjuntos', 'Enumera los documentos que adjuntarás, separados por comas. Si no hay, escribe «ninguno».'),
    ('folios', '¿Cuántos folios adjuntas? Escribe un número o «por completar».'),
]
FIELD_KEYS = [key for key, _ in FIELDS]
PROMPTS = dict(FIELDS)
OPTIONAL = {'escuela', 'codigo', 'direccion', 'celular', 'correo', 'adjuntos', 'folios'}
SCHOOLS = [
    'Escuela Profesional de Administración',
    'Escuela Profesional de Ingeniería Agroindustria',
    'Escuela Profesional de Ingeniería de Minas',
    'Escuela Profesional de Ingeniería Informática y Sistemas',
    'Escuela Profesional de Ingeniería Civil',
    'Escuela Profesional de Ingeniería Agroecológica y Desarrollo Rural',
    'Escuela Profesional de Medicina Veterinaria y Zootecnia',
    'Escuela Profesional de Ciencia Política y Gobernabilidad',
    'Escuela Profesional de Educación Inicial Intercultural Bilingüe',
]


def _plain(value):
    value = unicodedata.normalize('NFKD', str(value).casefold())
    return re.sub(r'[^a-z0-9 ]+', ' ', ''.join(c for c in value if not unicodedata.combining(c))).strip()


def _yes(value):
    words = _plain(value).split()
    if not words:
        return False
    if words[0] in {'correcto', 'confirmo', 'ok', 'okay', 'vale', 'claro', 'exacto', 'listo'}:
        return set(words[1:]) <= {'por', 'favor', 'porfa', 'porfavor', 'gracias'}
    return bool(re.fullmatch(r'si+|sip|sipi|sep', words[0])) and set(words[1:]) <= {'por', 'favor', 'porfa', 'porfavor', 'gracias', 'claro'}


def _no(value):
    return _plain(value) in {'no', 'nop', 'no gracias', 'otro', 'otra', 'cambiar', 'corregir'}


def _confirm_candidate(value, candidate):
    if _yes(value):
        return True
    plain = _plain(value)
    charge = _plain(candidate.get('cargo', ''))
    name = _plain(candidate.get('nombre', ''))
    return bool(re.match(r'^(si+|sip|sipi|sep)\b', plain) and
                (any(word in plain for word in charge.split() if len(word) >= 5) or
                 any(word in plain for word in name.split() if len(word) >= 5)))


def _type_from_text(value, rows):
    plain = _plain(value)
    if re.search(r'\bestudi\w*\b', plain) and ('constan' in plain or 'soncantica' in plain):
        return next(row for row in rows if row['id'] == 'constancia_estudios')
    if 'similitud' in plain:
        return next(row for row in rows if row['id'] == 'filtro_similitud')
    if 'rectific' in plain and ('datos' in plain or 'academic' in plain):
        return next(row for row in rows if row['id'] == 'rectificacion_datos')
    for row in rows:
        if SequenceMatcher(None, plain, _plain(row['tipo'])).ratio() >= 0.72:
            return row
    return None


def _school(value):
    plain = _plain(value)
    if plain in {'omitir', 'no corresponde'}:
        return ''
    aliases = [
        ({'administracion', 'admin'}, 0),
        ({'agroindustria', 'agroindustrial'}, 1),
        ({'minas', 'mineria'}, 2),
        ({'sistemas', 'informatica', 'computacion'}, 3),
        ({'civil'}, 4),
        ({'agroecologica', 'agroecologia', 'desarrollo rural'}, 5),
        ({'veterinaria', 'zootecnia'}, 6),
        ({'politica', 'gobernabilidad'}, 7),
        ({'inicial', 'intercultural bilingue'}, 8),
    ]
    for words, index in aliases:
        if any(word in plain for word in words):
            return SCHOOLS[index]
    matches = [name for name in SCHOOLS if SequenceMatcher(None, plain, _plain(name).replace('escuela profesional de ', '')).ratio() >= 0.72]
    if len(matches) == 1:
        return matches[0]
    try:
        answer = ollama.Client(host=config.OLLAMA_HOST, timeout=35).chat(
            model=config.CHAT_MODEL,
            messages=[{'role': 'system', 'content': 'Identifica una carrera universitaria entre las opciones enumeradas. Responde solo JSON con indice (1 a 9, o 0 si no es clara) y confianza (0 a 1). No inventes carreras. Si el texto no describe una carrera, usa 0.'},
                      {'role': 'user', 'content': json.dumps({'mensaje': value, 'opciones': list(enumerate(SCHOOLS, 1))}, ensure_ascii=False)}],
            format='json', options={'temperature': 0, 'num_predict': 80}, stream=False)
        result = json.loads(answer.message.content)
        index = int(result.get('indice', 0))
        if 1 <= index <= len(SCHOOLS) and float(result.get('confianza', 0)) >= 0.85:
            return SCHOOLS[index - 1]
    except Exception:
        pass
    raise ValueError('No identifico con seguridad la carrera. Dime el nombre o una palabra distintiva, como «sistemas» o «minas».')


def _summary(value, type_id):
    plain = _plain(value)
    if type_id == 'solicitud_libre' and re.search(r'\b(?:notas|calificaciones)\s+finales\b', plain) and not re.search(r'\b(?:rectific|correg|correcci|revis)\w*', plain):
        return 'Solicito la entrega de mis calificaciones finales.'
    if type_id == 'constancia_estudios' and any(word in plain for word in ('estudios', 'constan', 'constn', 'soncantica')):
        if 'bachiller' in plain:
            return 'Solicito una constancia de estudios para continuar mi trámite de bachiller.'
        if 'trabaj' in plain or 'empleo' in plain:
            return 'Solicito una constancia de estudios para presentarla en un proceso laboral.'
        if 'practic' in plain and 'comedor' in plain:
            return 'Solicito una constancia de estudios para acceder al comedor universitario y realizar mis prácticas preprofesionales.'
        if 'practic' in plain:
            return 'Solicito una constancia de estudios para realizar mis prácticas preprofesionales.'
        if 'comedor' in plain:
            return 'Solicito una constancia de estudios para acceder al comedor universitario.'
        return 'Solicito la emisión de mi constancia de estudios.'
    if type_id == 'filtro_similitud' and 'similitud' in plain:
        return 'Solicito la emisión de una constancia de filtro de similitud.'
    if type_id == 'rectificacion_datos' and 'rectific' in plain:
        return 'Solicito la rectificación de mis datos académicos.'
    return value


@dataclass
class Conversation:
    step: str = 'tipo'
    data: dict = field(default_factory=dict)
    options: list = field(default_factory=list)
    candidate: dict = field(default_factory=dict)
    draft: str = ''
    reason: str = ''
    details: str = ''
    prefilled: set = field(default_factory=set)
    updated: float = field(default_factory=time.monotonic)


def _clean():
    now = time.monotonic()
    for key in list(SESSIONS):
        if now - SESSIONS[key].updated > SESSION_TTL:
            del SESSIONS[key]
    for key in list(DOWNLOADS):
        if now - DOWNLOADS[key][0] > DOWNLOAD_TTL:
            del DOWNLOADS[key]
    while len(SESSIONS) > 200:
        del SESSIONS[next(iter(SESSIONS))]


def _reply(message, **extra):
    return {'reply': message, **extra}


def _types(rows):
    lines = [f'{i}. {row["tipo"]}' for i, row in enumerate(rows, 1)]
    return 'Cuéntame qué trámite necesitas o elige una opción:\n' + '\n'.join(lines) + '\nTambién puedes escribir /cancelar en cualquier momento.'


def _validate(key, value):
    value = value.strip()
    if value.casefold() in {'omitir', 'ninguno', 'por completar'} and key in OPTIONAL:
        return [] if key == 'adjuntos' else ''
    if not value:
        raise ValueError('Necesito ese dato para continuar.')
    if key not in OPTIONAL and value.casefold() in {'no se', 'no sé', 'nose', 'ni idea', 'desconozco'}:
        raise ValueError('No quiero poner «no sé» en tu FUT. Dime ese dato o cuéntame qué conoces para ayudarte.')
    if len(value) > (1200 if key == 'fundamentacion' else 250):
        raise ValueError('El texto es demasiado largo; resúmelo para que quepa en el FUT.')
    if key == 'codigo':
        value = re.sub(r'^c[oó]digo\s*:\s*', '', value, flags=re.I)
    if key == 'celular':
        value = re.sub(r'^\+51\s*', '', value)
        value = re.sub(r'[\s-]', '', value)
    if key == 'dni' and not re.fullmatch(r'\d{8}', value):
        raise ValueError('El DNI debe tener exactamente 8 dígitos.')
    if key == 'codigo' and not re.fullmatch(r'[a-zA-Z0-9-]{1,20}', value):
        raise ValueError('Escribe solo el código de estudiante, sin el texto «Código», o «omitir».')
    if key == 'celular' and not re.fullmatch(r'\d{7,9}', value):
        raise ValueError('Escribe solo el número de contacto, de 7 a 9 dígitos, o «omitir».')
    if key == 'correo' and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
        raise ValueError('Escribe un correo válido o «omitir».')
    if key == 'folios' and (not value.isdigit() or not 0 <= int(value) <= 9999):
        raise ValueError('Escribe el número de folios, hasta 9999, o «por completar».')
    if key == 'adjuntos':
        plain = _plain(value)
        if plain in {'no no tengo ninguno', 'no tengo ninguno', 'no tengo ninguno que adjuntar', 'no tengo nada', 'nada', 'ninguno', 'ninguna', 'sin adjuntos', 'no adjunto nada'}:
            return []
        return [part.strip() for part in re.split(r'[,;\n]+', value) if part.strip()][:10]
    if key == 'nombre':
        plain = _plain(value)
        if len(value.split()) < 2 or any(phrase in plain for phrase in ('ponlo', 'ponlo en el fut', 'escribelo', 'no se', 'tu sabes', 'mi nombre', 'soy yo', 'pon mi nombre')) or re.search(r'\d', value):
            raise ValueError('Necesito tu nombre y apellidos reales. No tengo acceso a tus datos personales; escríbelos tal como deben aparecer en el FUT.')
    if key == 'escuela':
        return _school(value)
    return value


def _polish(reason, summary, detailed=False):
    grounded = contexto_fut.compose_reason(summary, reason, '')
    if grounded:
        return grounded
    if 'constancia de estudios' in _plain(summary):
        plain = _plain(reason + ' ' + summary)
        purposes = []
        if 'comedor' in plain:
            purposes.append('acceder al comedor universitario')
        if 'practic' in plain:
            purposes.append('realizar mis prácticas preprofesionales')
        if purposes:
            return 'Solicito una constancia de estudios para ' + ' y '.join(purposes) + '.'
        if 'constan' in plain or 'constn' in plain:
            return 'Solicito la emisión de mi constancia de estudios.'
    try:
        response = ollama.Client(host=config.OLLAMA_HOST, timeout=90).chat(
            model=config.CHAT_MODEL,
            messages=[{'role': 'system', 'content': 'Eres asistente de redacción para un FUT universitario. Propón una fundamentación en primera persona, de una a tres oraciones, clara y formal. Usa únicamente el pedido y los hechos escritos por la persona. Si un detalle es ambiguo, no lo incluyas. No inventes personas, cursos, documentos, pagos, normas, fechas ni requisitos. Devuelve solo la propuesta.'},
                      {'role': 'user', 'content': f'Pedido: {summary}\nMotivo escrito por la persona: {reason}\nRedacta la fundamentación.'}],
            options={'temperature': 0, 'num_predict': 250}, stream=False)
        suggestion = (response.message.content or '').strip().strip('"')
        if not suggestion or any(phrase in suggestion.casefold() for phrase in ('motivo indicado', 'para el presentación', 'como ia', 'no puedo')):
            return reason
        original_numbers = set(re.findall(r'\d+', reason + ' ' + summary))
        if set(re.findall(r'\d+', suggestion)) - original_numbers:
            return reason
        return suggestion[:1200]
    except Exception:
        return reason


def _detail_question(reason, summary):
    plain = _plain(reason + ' ' + summary)
    if 'practic' in plain:
        return 'Entiendo que la necesitas para tus prácticas preprofesionales. ¿Hay algún requisito o proceso concreto que quieras mencionar? Si no tienes más detalles, escribe «sin más datos».'
    if 'comedor' in plain:
        return 'Entiendo que la necesitas para el comedor universitario. ¿Hay algún requisito o circunstancia concreta que deba incluir? Si no, escribe «sin más datos».'
    return 'Para fundamentar mejor el pedido, ¿hay algún hecho, requisito o plazo concreto que debamos mencionar? Si no tienes más detalles, escribe «sin más datos».'


def _review(data):
    attachments = ', '.join(data.get('adjuntos') or []) or 'Ninguno indicado'
    return ('Revisa tu borrador antes de crear el PDF:\n'
            f'Trámite: {data.get("tipo", "")}\nPedido: {data["resumen"]}\n'
            f'Destinatario: {data["destinatario"]} · {data["cargo"]}\n'
            f'Solicitante: {data["nombre"]} · DNI terminado en {data["dni"][-4:]}\n'
            f'Escuela: {data.get("escuela") or "No indicada"} · Código: {data.get("codigo") or "No indicado"}\n'
            f'Domicilio: {data.get("direccion") or "No indicado"} · {data.get("distrito", "")}, {data.get("provincia", "")}, {data.get("departamento", "")}\n'
            f'Contacto: {data.get("celular") or "No indicado"} · {data.get("correo") or "Correo no indicado"}\n'
            f'Fundamentación: {data["fundamentacion"]}\nAdjuntos: {attachments}\n'
            f'Folios: {data.get("folios") or "Por completar"}\n'
            'Escribe /generar para descargar el FUT. Para corregir: campo: nuevo valor (por ejemplo, destinatario: Nombre actual).')


def _advance(state, key):
    index = FIELD_KEYS.index(key) + 1
    while index < len(FIELDS) and FIELD_KEYS[index] in state.prefilled:
        index += 1
    if index == len(FIELDS):
        issue = _consistency(state.data)
        if issue:
            state.step = 'consistencia'
            return issue + '\nPuedes corregirlo con «resumen: ...» o «fundamentacion: ...». Si los datos son correctos, escribe /continuar.'
        state.step = 'review'
        return _review(state.data)
    state.step = FIELD_KEYS[index]
    return PROMPTS[state.step]


def _looks_like_role(value):
    plain = value.casefold()
    return bool(re.search(r'\b(no s[eé]|qui[eé]n|encargad[oa]|director[ao]|jef[ea]|responsable|área|area|unidad|dependencia|rector|decano)\b', plain))


def _consistency(data):
    summary = data.get('resumen', '')
    reason = data.get('fundamentacion', '')
    if 'rectific' in summary.casefold() and 'comedor' in reason.casefold() and 'rectific' not in reason.casefold():
        return 'Veo una diferencia: el resumen pide rectificar datos académicos, pero el motivo habla del comedor universitario. ¿Cuál es el trámite que quieres presentar?'
    try:
        response = ollama.Client(host=config.OLLAMA_HOST, timeout=45).chat(
            model=config.CHAT_MODEL,
            messages=[{'role': 'system', 'content': 'Compara únicamente el resumen del pedido y la fundamentación. Detecta contradicción clara de tema. No evalúes si la autoridad es correcta. Responde JSON con conflict (booleano) y pregunta (una pregunta breve en español si hay conflicto). Si falta información pero no hay contradicción, conflict=false.'},
                      {'role': 'user', 'content': json.dumps({'resumen': summary, 'fundamentacion': reason}, ensure_ascii=False)}],
            format='json', options={'temperature': 0, 'num_predict': 130}, stream=False)
        result = json.loads(response.message.content)
        if result.get('conflict') is True:
            return str(result.get('pregunta') or 'El resumen y el motivo parecen referirse a trámites distintos. ¿Cuál deseas presentar?')[:350]
    except Exception:
        pass
    return ''


def _suggest_official(state, query):
    try:
        matches = funcionarios.find(query)
    except Exception:
        matches = []
    if not matches or matches[0]['coincidencia'] < 0.43:
        return 'No encuentro una coincidencia suficientemente clara en la lista oficial. Dime el nombre exacto del área o dependencia, o el nombre de la persona.'
    best = matches[0]
    state.candidate = best
    state.step = 'destinatario_confirmar'
    alternatives = ''
    if len(matches) > 1 and matches[1]['coincidencia'] >= best['coincidencia'] - 0.07:
        other = matches[1]
        alternatives = f' También aparece {other["nombre"]} ({other["cargo"]}); si te refieres a esa persona, escribe su nombre.'
    return (f'En el directorio oficial figura **{best["nombre"]}**, {best["cargo"]}. '
            f'¿Te refieres a esta persona? Responde sí para usar ese nombre y cargo, o dime el área correcta.{alternatives}')


def respond(session_id, message):
    with LOCK:
        _clean()
        if not isinstance(session_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', session_id):
            raise ValueError('Sesión no válida.')
        text = str(message or '').strip()[:1200]
        if text.casefold() in {'/cancelar', '/reiniciar'}:
            SESSIONS.pop(session_id, None)
            return _reply('Borrador descartado. Escribe /iniciar cuando quieras crear otro FUT.')
        state = SESSIONS.setdefault(session_id, Conversation())
        state.updated = time.monotonic()
        extracted = {}
        if state.step == 'nombre' or re.search(r'\b(correo|c[oó]digo|celular|n[uú]mero|dni|tel[eé]fono|me llamo|mi nombre es)\b', text, re.I):
            extracted = contexto_fut.extract_fields(text)
            if len(extracted) < 2 and len(re.findall(r'\b(correo|c[oó]digo|celular|n[uú]mero|dni|direcci[oó]n)\b', text, re.I)) >= 2:
                for field_key, field_value in contexto_fut.extract_ambiguous(text, state.data).items():
                    extracted.setdefault(field_key, field_value)
        if state.step in {'tipo', 'resumen', 'nombre', 'direccion', 'distrito', 'provincia', 'departamento'}:
            extracted.update(contexto_fut.extract_domicile(text))
        if state.step in {'distrito', 'provincia'} and _plain(text) == 'abancay':
            extracted.update(contexto_fut.ABANCAY)
        if state.step == 'direccion' and re.fullmatch(r'(?:(?:vivo|resido) en |soy de )?abancay(?: apurimac)?', _plain(text)):
            extracted.update(contexto_fut.ABANCAY, direccion='')
        for field_key, field_value in list(extracted.items()):
            try:
                cleaned = field_value if field_key == 'direccion' and not field_value else _validate(field_key, field_value)
            except ValueError:
                del extracted[field_key]
                continue
            if field_key not in state.data or state.step == field_key:
                state.data[field_key] = cleaned
                state.prefilled.add(field_key)
        if state.step == 'tipo':
            rows = catalogo.entries()
            if text and text.casefold() not in {'/iniciar', '/tipos'} and not text.isdigit():
                request = contexto_fut.request_text(text)
                chosen = _type_from_text(request or text, rows)
                if request and not chosen:
                    chosen = next(row for row in rows if row['id'] == 'solicitud_libre')
                if chosen:
                    state.data.update({'tipo': chosen['tipo'], 'tipo_id': chosen['id']})
                    if request:
                        summary = _summary(request, chosen['id'])
                        state.data['resumen'] = summary[0].upper() + summary[1:]
                        state.step = 'destinatario'
                        role = contexto_fut.extract_role(text)
                        understood = f'Anoté el pedido: «{state.data["resumen"].rstrip(".")}». '
                        if role:
                            return _reply(understood + _suggest_official(state, role))
                        return _reply(understood + PROMPTS['destinatario'])
                    state.step = 'resumen'
                    return _reply(f'Entendido: «{chosen["tipo"]}». {PROMPTS["resumen"]}')
                matches = catalogo.best_match(text)
                choices = [next((row for row in rows if row['id'] == meta['id']), None) for meta, _ in matches]
                state.options = [row for row in choices if row]
                if state.options:
                    return _reply(_types(state.options))
                return _reply(_types(rows))
            choices = state.options or rows
            if not text.isdigit():
                state.options = rows
                catalogo.sync()
                return _reply(_types(rows))
            index = int(text) - 1
            if index < 0 or index >= len(choices):
                return _reply(_types(choices))
            chosen = choices[index]
            state.data.update({'tipo': chosen['tipo'], 'tipo_id': chosen['id']})
            state.step = 'resumen'
            return _reply(f'Perfecto, trabajaremos en «{chosen["tipo"]}». ' + PROMPTS['resumen'])
        if state.step == 'destinatario_confirmar':
            if _confirm_candidate(text, state.candidate):
                state.data['destinatario'] = state.candidate['nombre']
                state.data['cargo'] = state.candidate['cargo']
                state.data['fuente_destinatario'] = state.candidate.get('fuente', '')
                state.candidate = {}
                next_prompt = _advance(state, 'cargo')
                return _reply(f'Anoté {state.data["destinatario"]} y su cargo. ' + next_prompt)
            if _no(text):
                state.step = 'destinatario'
                state.candidate = {}
                return _reply('Entendido. ¿Qué cargo o área buscas exactamente?')
            if _plain(text).startswith(('si', 'sii', 'ok')):
                return _reply(f'Para confirmar: ¿quieres dirigirlo a {state.candidate["nombre"]}, {state.candidate["cargo"]}? Puedes responder «sí» o «no».')
            state.step = 'destinatario'
            state.candidate = {}
            return _reply(_suggest_official(state, text))
        if state.step == 'fundamentacion_confirmar':
            if contexto_fut.wants_rewrite(text):
                improved = contexto_fut.compose_reason(state.data['resumen'], state.reason, state.details, polished=True)
                if improved:
                    state.draft = improved
                    return _reply(f'Claro. Lo dejaría así, usando solo lo que me contaste:\n\n«{state.draft}»\n\n¿Ahora expresa bien tu motivo?')
                return _reply('Claro. ¿Qué parte quieres mejorar o qué hecho falta? Dímelo y preparo otra versión sin inventar datos.')
            if _yes(text):
                state.data['fundamentacion'] = state.draft
                state.draft = ''
                return _reply('Bien, usaré esa redacción. ' + _advance(state, 'fundamentacion'))
            if _no(text):
                state.step = 'fundamentacion'
                return _reply('De acuerdo. Escribe cómo quieres expresar el motivo; no agregaré hechos que no me indiques.')
            state.data['fundamentacion'] = _validate('fundamentacion', text)
            state.draft = ''
            return _reply('Usaré tus palabras. ' + _advance(state, 'fundamentacion'))
        if state.step == 'fundamentacion_detalles':
            plain = _plain(text)
            no_details = plain in {'sin mas datos', 'no', 'ninguno', 'nada', 'no tengo mas datos', 'no se', 'omitir'}
            details = '' if no_details else text.strip()
            state.details = details
            if len(details) > 450:
                return _reply('Ese detalle es muy largo para el FUT. Resúmelo en una o dos frases, o escribe «sin más datos».')
            state.draft = contexto_fut.compose_reason(state.data['resumen'], state.reason, details) or _polish(state.reason, state.data['resumen'])
            if details and not contexto_fut.compose_reason(state.data['resumen'], state.reason, details):
                detail_sentence = details.strip().rstrip('.!?')
                detail_sentence = detail_sentence[0].upper() + detail_sentence[1:]
                state.draft += ' ' + detail_sentence + '.'
            state.step = 'fundamentacion_confirmar'
            return _reply(f'Con lo que me contaste, propongo esta fundamentación:\n\n«{state.draft}»\n\n¿Expresa correctamente tu motivo? Responde sí o escribe cómo quieres cambiarla.')
        if state.step == 'consistencia':
            if text.casefold() == '/continuar':
                state.step = 'review'
                return _reply(_review(state.data))
            match = re.fullmatch(r'(resumen|fundamentacion):\s*(.+)', text, re.I | re.S)
            if match:
                key = match[1].lower()
                try:
                    state.data[key] = _validate(key, match[2])
                except ValueError as exc:
                    return _reply(str(exc))
                issue = _consistency(state.data)
                if issue:
                    return _reply(issue + '\nCorrige el resumen o la fundamentación, o escribe /continuar si son correctos.')
                state.step = 'review'
                return _reply(_review(state.data))
            return _reply('Antes de generar el documento, aclaremos el pedido. Escribe «resumen: ...», «fundamentacion: ...» o /continuar si ambos datos son correctos.')
        if state.step == 'review':
            if text.casefold() == '/generar' or _yes(text):
                try:
                    pdf = render(state.data)
                except ValueError as exc:
                    return _reply(f'{exc} Puedes corregir el campo con «campo: nuevo valor» y volver a generar.')
                token = secrets.token_urlsafe(32)
                DOWNLOADS[token] = (time.monotonic(), pdf)
                state.step = 'done'
                return _reply('Tu FUT está listo para descargar. Revísalo y fírmalo antes de presentarlo.', download_token=token)
            match = re.fullmatch(r'([a-záéíóúñ_ ]+):\s*(.+)', text, re.I | re.S)
            if not match:
                return _reply(_review(state.data))
            key = match[1].strip().lower().replace(' ', '_')
            if key not in FIELD_KEYS:
                return _reply('Campo no reconocido. Puedes corregir: ' + ', '.join(FIELD_KEYS))
            try:
                state.data[key] = _validate(key, match[2])
            except ValueError as exc:
                return _reply(str(exc))
            return _reply(_review(state.data))
        if state.step == 'done':
            return _reply('El FUT ya fue generado. Escribe /reiniciar para crear otro.')
        key = state.step
        if key == 'destinatario' and _looks_like_role(text):
            return _reply(_suggest_official(state, text))
        if key == 'cargo' and _looks_like_role(text):
            return _reply(_suggest_official(state, text))
        input_value = extracted.get(key, text)
        if key == 'direccion':
            input_value = contexto_fut.clean_address(input_value) or 'omitir'
        if key == 'resumen':
            if _yes(text) or re.fullmatch(r'\d+(?:[.)]\s*.+)?', text):
                return _reply('Cuéntame qué documento o acción necesitas. Por ejemplo: «Solicito que me entreguen mis calificaciones finales».')
            input_value = contexto_fut.request_text(text) or input_value
        try:
            value = _validate(key, input_value)
        except ValueError as exc:
            return _reply(str(exc) + '\n' + PROMPTS[key])
        if key == 'resumen':
            lowered = value.casefold()
            for row in catalogo.entries():
                marker = row['tipo'].casefold()
                if marker in lowered or (row['id'] == 'rectificacion_datos' and 'rectific' in lowered):
                    if row['id'] != state.data.get('tipo_id'):
                        state.data['tipo'], state.data['tipo_id'] = row['tipo'], row['id']
                        state.data[key] = _summary(value, row['id'])
                        return _reply(f'Por lo que escribiste, el trámite corresponde a «{row["tipo"]}». Lo ajusté en el borrador. ' + _advance(state, key))
                    break
            value = _summary(value, state.data.get('tipo_id'))
            role = contexto_fut.extract_role(text)
            if role:
                state.data[key] = value
                state.step = 'destinatario'
                return _reply(f'Anoté el pedido: «{value.rstrip(".")}». ' + _suggest_official(state, role))
        if key == 'departamento' and _plain(value) == 'abancay':
            return _reply('Abancay es el distrito y la provincia que indicaste. Para el departamento, ¿corresponde «Apurímac»? Escribe «Apurímac» si es correcto.')
        if key == 'fundamentacion':
            state.reason = value
            state.step = 'fundamentacion_detalles'
            return _reply(_detail_question(value, state.data['resumen']))
        state.data[key] = value
        next_prompt = _advance(state, key)
        if key == 'nombre' and len(extracted) > 1:
            captured = [label for field_key, label in (('correo', 'correo'), ('celular', 'celular'), ('codigo', 'código'), ('dni', 'DNI')) if field_key in extracted]
            return _reply(f'Anoté tu nombre y también {", ".join(captured)}. Los podrás revisar antes de generar el FUT. ' + next_prompt)
        if key == 'resumen' and value != text:
            return _reply(f'Para que quede claro en el FUT, redacté el pedido así: «{value}». Si quieres cambiarlo, podrás corregirlo antes de generar. ' + next_prompt)
        if key == 'escuela' and value and value != text:
            return _reply(f'Anotaré «{value}». Si no es tu carrera, dímelo y la corregimos antes de generar. ' + next_prompt)
        return _reply(next_prompt)


def download(token):
    with LOCK:
        _clean()
        record = DOWNLOADS.get(token)
        return record[1] if record else None
