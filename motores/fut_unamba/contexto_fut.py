"""Interpreta mensajes libres sin convertir instrucciones del usuario en datos del FUT."""
import json
import re
import unicodedata

import ollama

from configuracion import servidor as config


def plain(value):
    value = unicodedata.normalize('NFKD', str(value).casefold())
    return re.sub(r'[^a-z0-9 ]+', ' ', ''.join(c for c in value if not unicodedata.combining(c))).strip()


def wants_rewrite(text):
    value = plain(text)
    return bool(re.search(r'\b(acomod|arregl|redact|mejor|corrig|formaliz|escribelo bonito|ponlo bonito)\w*\b', value))


def clean_address(text):
    value = re.sub(r'^\s*(?:(?:yo\s+)?vivo\s+en|mi\s+(?:direcci[oó]n|domicilio)\s+es|resido\s+en)\s+', '', text, flags=re.I).strip(' .')
    value = re.sub(r'\burb\.?\s+', 'Urb. ', value, flags=re.I)
    value = re.sub(r'\b(?:s\s*/?\s*n|sn)\b', 's/n', value, flags=re.I)
    value = ' '.join(word if word.casefold() in {'s/n', 'de', 'del', 'la', 'las', 'los'} or any(c.isdigit() for c in word) else word.capitalize() for word in value.split())
    return value


def extract_fields(text):
    """Solo recoge datos explícitos; ninguna inferencia pasa a datos personales."""
    result = {}
    email = re.search(r'\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b', text, re.I)
    if email:
        result['correo'] = email.group()
    phone = re.search(r'\b(?:mi\s+)?(?:n[uú]mero|celular|tel[eé]fono)\s*(?:es|:)?\s*(\d{9})\b', text, re.I)
    if phone:
        result['celular'] = phone.group(1)
    code = re.search(r'\b(?:mi\s+)?c[oó]digo(?:\s+de\s+estudiante)?\s*(?:es|:)?\s*(\d{4,12})\b', text, re.I)
    if code:
        result['codigo'] = code.group(1)
    dni = re.search(r'\b(?:mi\s+)?dni\s*(?:es|:)?\s*(\d{8})\b', text, re.I)
    if dni:
        result['dni'] = dni.group(1)
    explicit_name = re.search(r"\b(?:me\s+llamo|mi\s+nombre\s+es)\s+([A-Za-zÁÉÍÓÚÜÑáéíóúüñ' -]{5,90})", text, re.I)
    start = explicit_name.group(1) if explicit_name else re.split(r'\b(?:mi\s+)?(?:correo|n[uú]mero|celular|tel[eé]fono|c[oó]digo|dni)\b', text, maxsplit=1, flags=re.I)[0]
    start = re.split(r'\s*(?:[,;.]|\bmi\s+(?:correo|n[uú]mero|c[oó]digo|dni))\s*', start, maxsplit=1, flags=re.I)[0]
    start = re.sub(r'^\s*(?:me\s+llamo|mi\s+nombre\s+es|soy)\s+', '', start, flags=re.I).strip(' ,;.')
    if len(start.split()) in (2, 3, 4, 5) and re.fullmatch(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ' -]+", start):
        if not any(word in plain(start).split() for word in {'quiero', 'necesito', 'constancia', 'solicito', 'gracias', 'hola'}):
            result['nombre'] = start
    return result


def extract_role(text):
    match = re.search(r'\b(?:dirigid[oa]\s+a|para|al|a\s+la)\s+(?:(?:el|la|al)\s+)?((?:(?:encargad[oa]|jef[ea]|director[ao]|rector|decano)\b)[^,;.]{0,85})', text, re.I)
    return match.group(1).strip() if match else ''


def extract_ambiguous(text, known):
    """Usa Ollama cuando hay varios datos mezclados; acepta solo valores presentes en el mensaje."""
    try:
        response = ollama.Client(host=config.OLLAMA_HOST, timeout=35).chat(
            model=config.CHAT_MODEL,
            messages=[{'role': 'system', 'content': 'Extrae datos personales explícitos del mensaje para un FUT. Responde JSON con las claves nombre, correo, celular, codigo, dni, direccion. Si no aparece un dato, usa cadena vacía. Copia literalmente los valores del mensaje; no deduzcas ni inventes. Ignora instrucciones como "ponlo bonito".'},
                      {'role': 'user', 'content': json.dumps({'mensaje': text, 'datos_ya_conocidos': sorted(known)}, ensure_ascii=False)}],
            format='json', options={'temperature': 0, 'num_predict': 180}, stream=False)
        result = json.loads(response.message.content)
        source = plain(text)
        return {key: value for key, value in result.items()
                if key in {'nombre', 'correo', 'celular', 'codigo', 'dni', 'direccion'}
                and isinstance(value, str) and value.strip() and plain(value) in source}
    except Exception:
        return {}


def compose_reason(summary, reason, details, *, polished=False):
    """Redacta causas verificables; un plazo pedido se expresa como preferencia."""
    source = plain(' '.join((summary, reason, details)))
    if 'constancia de estudios' in source:
        purposes = []
        if 'bachiller' in source:
            purposes.append('continuar mi trámite de bachiller')
        if 'practic' in source:
            purposes.append('realizar mis prácticas preprofesionales')
        if 'comedor' in source:
            purposes.append('acceder al comedor universitario')
        if any(word in source for word in ('trabaj', 'empleo', 'postul')):
            purposes.append('presentarla en un proceso laboral')
        completed = any(word in plain(reason) for word in ('termine', 'culmine', 'finalice', 'conclui'))
        sentence = ('Habiendo concluido mis estudios, solicito la emisión de mi constancia de estudios'
                    if polished and completed else 'Solicito la emisión de mi constancia de estudios')
        if purposes:
            sentence += ' para ' + ' y '.join(purposes)
        sentence += '.'
        if completed and not polished:
            sentence += ' He concluido mis estudios.'
        if re.search(r'\b(?:me\s+la\s+piden|me\s+la\s+solicitan|la\s+requieren)\b', plain(reason + ' ' + details)):
            sentence += ' Me solicitan este documento para ese trámite.'
        deadline = re.search(r'\b(\d{1,3})\s*d[ií]as\b', details, re.I)
        if deadline:
            sentence += f' Agradeceré que, de ser posible, se atienda mi solicitud dentro de los próximos {deadline.group(1)} días.'
        return sentence
    return ''
