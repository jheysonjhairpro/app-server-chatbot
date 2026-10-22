"""Respuestas fijas para saludos, agradecimientos y preguntas sobre el propio bot."""

import re
import unicodedata

GREETING_RE = re.compile(
    r"^(?:\s*(?:hola+|buen[oa]s|dias|tardes|noches|hey|saludos|que tal|a todos|amigos?|estimados?)[\s!.,¡¿?]*)+$"
)
THANKS_RE = re.compile(r"^\s*(?:muchas |mil |muchisimas )?gracias(?:\s+por (?:tu ayuda|la ayuda|la informacion|todo))?[\s!.,¡]*$")
BYE_RE = re.compile(r"^\s*(?:chau|chao|adios|hasta luego|nos vemos|hasta pronto)(?:\s+y\s+gracias)?[\s!.,¡]*$")
HELP_RE = re.compile(
    r"^\s*(?:que puedes hacer|quien eres|que eres|como me puedes ayudar|en que me puedes ayudar|ayuda|ayudame)\s*[?¡!.]*\s*$"
)

GREETING = (
    "¡Hola! Soy el asistente virtual informativo de la UNAMBA. Puedo ayudarte con admisión, carreras, "
    "CPU, posgrado, idiomas, servicios y trámites. ¿Qué deseas saber?"
)
THANKS = "¡Con gusto! Si tienes otra consulta sobre la UNAMBA, escríbela cuando quieras."
BYE = "¡Hasta pronto! Aquí estaré cuando necesites información de la UNAMBA."
HELP = (
    "Soy el asistente virtual informativo de la UNAMBA. Respondo únicamente con la información de los "
    "documentos oficiales cargados: admisión, vacantes, cronogramas, carreras y facultades, CPU, posgrado, "
    "centros de idiomas e informática, biblioteca, comedor, pagos y contactos. Pregúntame con naturalidad."
)


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch)).strip()


def small_talk_reply(question: str) -> str | None:
    text = _normalize(question).strip("¿¡ \t")
    if not text or len(text.split()) > 8:
        return None
    if GREETING_RE.match(text):
        return GREETING
    if THANKS_RE.match(text):
        return THANKS
    if BYE_RE.match(text):
        return BYE
    if HELP_RE.match(text):
        return HELP
    if re.fullmatch(r'(?:donde estas|donde te encuentras|cual es tu ubicacion)[?!.]*', text):
        return 'Soy un asistente virtual y estoy disponible en este chatbot. Si buscas la ubicación de la UNAMBA, pregúntame por su sede o filial.'
    return None
