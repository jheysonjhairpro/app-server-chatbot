"""Conversación y búsqueda de frases, sin modelos ni acceso a la base de datos."""
import re

from motor_contexto import normalizar, palabras


AYUDA = ("Soy tu asistente bibliotecario de normativas de la UNAMBA. "
         "Puedo ayudarte a encontrar resoluciones, localizar frases y consultar artículos. "
         "Dime el número y año, un tema o una frase que recuerdes. "
         "También puedes escribir /pdfs para ver los documentos disponibles.")


def respuesta_social(texto):
    """Sólo mensajes sociales completos; un saludo con consulta sigue a búsqueda."""
    q = " ".join(palabras(texto))
    saludo = r"(?:hola+(?: papu| amigo| amiga)?|hey+|ey+|buenos dias|buenas tardes|buenas noches|buenas)"
    if re.fullmatch(saludo + r"(?: como estas| que tal)?", q):
        return "¡Hola! Estoy aquí para ayudarte a buscar en la biblioteca de resoluciones. " + AYUDA
    if re.fullmatch(r"como estas|que tal", q):
        return "¡Listo para ayudarte! ¿Buscamos una resolución por número, por tema o por alguna frase?"
    if re.fullmatch(r"(?:muchas )?gracias(?: por (?:tu ayuda|todo))?|ok(?: gracias)?|listo", q):
        return "¡Con gusto! Si necesitas otro artículo o resolución, dime cuál."
    if re.fullmatch(r"ayuda|que (?:haces|puedes hacer)|como (?:funcionas|te uso)|quien eres", q):
        return AYUDA
    if re.fullmatch(r"quien te (?:creo|hizo|desarrollo)", q):
        return "Soy el asistente de consulta documental de este sistema. No tengo configurado el nombre de su desarrollador. " + AYUDA
    if re.fullmatch(r"me gustas|(?:mi amor )?te (?:amo|quiero)(?: (?:mucho|demasiado))?", q):
        return "¡Gracias por el cariño! Estoy aquí para ayudarte con la biblioteca. ¿Qué documento buscamos?"
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
