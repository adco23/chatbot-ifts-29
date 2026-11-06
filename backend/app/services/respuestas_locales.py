"""Respuestas locales acotadas; no sustituyen evidencia ni clasifican con un LLM."""

import re

from app.services.sanitizador import normalizar_comparacion
from app.services.sinonimos import nombres_mencionados

SALUDO = (
    "Hola. Soy el asistente virtual del IFTS 29. Puedo orientarte sobre materias, "
    "horarios, cronogramas, trámites, certificados y otras consultas institucionales."
)
ALCANCE = (
    "Esa consulta está fuera del alcance del asistente. Puedo ayudarte con información "
    "académica y administrativa relacionada con el IFTS 29, como materias, horarios, "
    "cronogramas y trámites."
)
_SALUDO = re.compile(
    r"[¡¿]?(?:hola|buenas|buenos dias|buenas tardes|buenas noches)[!.?]*"
)
_CAPACIDADES = re.compile(
    r"(?:hola[,!.]* )?¿?(?:en que me (?:podes|puedes) ayudar|"
    r"que (?:podes|puedes) hacer|para que sirve este asistente)[?.!]*"
)
_RECETA = re.compile(
    r"¿?(?:(?:dame|pasame|mostrame|quiero|necesito)|como (?:hago|preparo)) "
    r"(?:la|una) receta (?:de|del|para) [a-z ]+[?.!]*"
)
_CONTEXTO_INSTITUCIONAL = re.compile(
    r"\b(?:ifts|materias?|carrera|programacion|programas?|sql|algoritmos?|estudiar|"
    r"certificados?|constancias?|tramites?|inscripcion(?:es)?|clases?|horarios?|cronogramas?|"
    r"examen(?:es)?|matematica|logica|bedelia|secretaria)\b"
)


def respuesta_local(texto: str) -> str | None:
    """Sólo mensajes completos inequívocos. La consulta pendiente no se descarta."""
    normalizado = normalizar_comparacion(texto)
    if _SALUDO.fullmatch(normalizado) or _CAPACIDADES.fullmatch(normalizado):
        return SALUDO
    if (_RECETA.fullmatch(normalizado) and not _CONTEXTO_INSTITUCIONAL.search(normalizado)
            and not nombres_mencionados(normalizado)):
        return ALCANCE
    return None
