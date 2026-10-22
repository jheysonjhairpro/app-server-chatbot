"""Conversación y búsqueda de frases, sin modelos ni acceso a la base de datos."""
import re

from motores.pdf.motor_contexto import normalizar, palabras


AYUDA = ("Soy un asistente virtual de normativas de la UNAMBA. "
         "Puedo ayudarte a encontrar resoluciones, localizar frases y consultar artículos. "
         "Dime el número y año, un tema o una frase que recuerdes. "
         "También puedes escribir /pdfs para ver los documentos disponibles.")


def respuesta_social(texto):
    """Responde con calidez a mensajes sociales completos; consultas siguen a búsqueda."""
    q = " ".join(palabras(texto))
    # Une una variante frecuente escrita sin espacio: "miamor".
    q = re.sub(r"\bmiamor\b", "mi amor", q)
    vocativo = r"(?:(?:mi )?(?:amor|vida|cielo|corazon|carino|bb|beb[eé]|bebecit[oa]|bbcit[oa]|papu|amigo|amiga))"
    saludo = r"(?:h?ola+|holi+|hey+|ey+|buenos dias|buenas tardes|buenas noches|buenas)"
    saludo_social = (saludo + r"(?: " + vocativo + r")?"
                     r"(?: como estas| que tal| como te va)?"
                     r"(?: " + vocativo + r")?")
    if re.fullmatch(saludo_social, q):
        return ("¡Hola! 😊 Estoy aquí para ayudarte con resoluciones y normativas de la UNAMBA. "
                "¿Qué necesitas buscar?")
    if re.fullmatch(r"como estas(?: " + vocativo + r")?|que tal(?: " + vocativo + r")?|como te va", q):
        return "¡Todo listo para ayudarte! 😊 ¿Buscamos una resolución por número, tema o alguna frase?"
    agradecimiento = r"(?:muchas )?(?:graciass*|grcias|grasias|graxias)(?: por (?:tu ayuda|todo))?"
    if re.fullmatch(agradecimiento + r"|ok(?: gracias)?|listo", q):
        return "¡Con gusto! 😊 Aquí estaré si necesitas consultar otra resolución."
    if re.fullmatch(r"bb", q):
        return "¡Hola! 😊 Estoy aquí para ayudarte con resoluciones y normativas de la UNAMBA. ¿Qué necesitas buscar?"
    if re.fullmatch(r"ayuda|que (?:haces|puedes hacer)|como (?:funcionas|te uso)|quien eres|para que sirves", q):
        return AYUDA
    if re.fullmatch(r"quien te (?:creo|hizo|desarrollo)", q):
        return "Soy el asistente virtual de consulta documental de este sistema. " + AYUDA
    declaracion = (r"(?:me gustas|me encantas|(?:te )?(?:amo|quiero)(?: mucho| demasiado)?|"
                   r"quieres ser mi (?:novia|novio|pareja)|seamos pareja|casate conmigo|"
                   r"estas enamorad[oa] de mi|te casarias conmigo)")
    palabras_carino = set(q.split())
    mensaje_carinoso = (
        bool(re.fullmatch(declaracion, q))
        or (palabras_carino <= {
            "como", "estas", "que", "tal", "mi", "amor", "vida", "cielo", "corazon",
            "carino", "bb", "bebecito", "bebecita", "bbcito", "bbcita", "de", "te",
            "amo", "quiero", "mucho", "demasiado", "me", "gustas", "encantas",
        } and bool(palabras_carino & {"amo", "quiero", "gustas", "encantas"}))
    )
    if mensaje_carinoso:
        return ("¡Qué lindo detalle, gracias! 😊 Soy un asistente virtual, así que no puedo tener una relación, "
                "pero sí puedo ayudarte a buscar resoluciones y normativas de la UNAMBA. ¿Qué documento necesitas?")
    if re.fullmatch(r"(?:eres )?(?:lind[oa]|hermos[oa]|bonit[oa]|guap[oa])", q):
        return "¡Gracias por el cumplido! 😊 Estoy aquí para ayudarte con resoluciones de la UNAMBA. ¿Qué buscamos?"
    if re.fullmatch(r"adios|hasta luego|chau|chao", q):
        return "¡Hasta luego! Aquí estaré cuando necesites consultar otra resolución."
    return None


def es_localizacion(texto):
    q = normalizar(texto)
    return bool(re.search(r"\b(?:donde (?:dice|menciona|aparece|habla|se menciona|se encuentra)|"
                          r"en que (?:resolucion|documento|pdf)|busca(?:r|me)?|encuentra(?:me)?|localiza)\b", q))


def busqueda_global(texto):
    """Las referencias deícticas conservan el documento elegido en la sesión."""
    q = normalizar(texto)
    return es_localizacion(texto) and not re.search(
        r"\b(?:(?:esta|este|esa|ese|misma|mismo|dicha|dicho) (?:resolucion|documento|pdf)|"
        r"en (?:ella|el)|aqui)\b", q)


def frase_buscada(texto):
    citas = re.findall(r'["“](.+?)["”]|«(.+?)»', texto)
    if citas:
        return max((a or b for a, b in citas), key=len)
    if es_localizacion(texto):
        cola = re.split(r"\b(?:dice|menciona|aparece|habla de|se encuentra|buscame|busca|buscar|encuentrame|encuentra|localiza)\b",
                        normalizar(texto), maxsplit=1)
        if len(cola) == 2:
            return re.sub(r"^\s*(?:la frase|el texto)\s*[:：]?\s*", "", cola[1]).strip(" ¿?.,:;")
    return ""


def coincidencias_frase(paginas, pregunta):
    frase = " ".join(palabras(frase_buscada(pregunta)))
    if len(frase.split()) < 2:
        return []
    return [p for p in paginas if " " + frase + " " in " " + " ".join(palabras(p.texto)) + " "]
