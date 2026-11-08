"""Respuestas locales acotadas; no sustituyen evidencia ni clasifican con un LLM."""

import json
import re
from functools import lru_cache
from pathlib import Path

from app.services.sanitizador import normalizar_comparacion
from app.services.sinonimos import nombres_mencionados

SALUDO = (
    "¡Hola! Soy el asistente virtual del IFTS 29. Puedo ayudarte con materias, comisiones, "
    "cronogramas, Moodle, plan de estudios y otras consultas académicas e institucionales. "
    "¿En qué te puedo ayudar?"
)
AGRADECIMIENTO = "¡De nada! Si necesitás otra información del IFTS 29, podés consultarme."
DESPEDIDA = "¡Hasta luego! Cuando necesites información del IFTS 29, podés volver a consultarme."
ALCANCE = (
    "Esa consulta está fuera del alcance del asistente. Puedo ayudarte con información "
    "académica y administrativa relacionada con el IFTS 29, como materias, horarios, "
    "cronogramas y trámites."
)
RUTA_SOCIALES = Path(__file__).resolve().parents[1] / "config" / "respuestas_locales.json"
_RESPALDO_SOCIAL = {
    "saludos": ["hola", "buenas"], "agradecimientos": ["gracias"], "despedidas": ["chau"],
}


@lru_cache(maxsize=8)
def cargar_respuestas_sociales(ruta: Path | None = None) -> dict[str, re.Pattern[str]]:
    """Catálogo local literal; recargar al reiniciar. Sin regex provenientes del JSON."""
    try:
        datos = json.loads((RUTA_SOCIALES if ruta is None else Path(ruta)).read_text(encoding="utf-8"))
        if not isinstance(datos, dict) or set(datos) != set(_RESPALDO_SOCIAL):
            raise ValueError("Catálogo social inválido.")
        for frases in datos.values():
            if (not isinstance(frases, list) or not frases
                    or any(not isinstance(f, str) or not normalizar_comparacion(f)
                           or not all(p.isalpha() for p in normalizar_comparacion(f).split())
                           for f in frases)):
                raise ValueError("Expresiones sociales inválidas.")
    except (OSError, UnicodeError, ValueError):
        datos = _RESPALDO_SOCIAL
    patrones = {}
    for categoria, frases in datos.items():
        # Sólo prolongaciones finales simples de cada palabra: hola -> holaaa.
        alternativas = {" ".join(re.escape(p[:-1]) + re.escape(p[-1]) + "+"
                                  for p in normalizar_comparacion(f).split()) for f in frases}
        frase = "(?:" + "|".join(sorted(alternativas, reverse=True)) + ")"
        patrones[categoria] = re.compile(rf"{frase}(?: {frase}){{0,2}}")
    return patrones
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


def respuesta_social(texto: str) -> str | None:
    """Saludo, agradecimiento o despedida completos; nunca una consulta mixta."""
    normalizado = normalizar_comparacion(texto)
    social = re.sub(r"[^\w\s]", " ", normalizado)
    social = " ".join(social.split())
    patrones = cargar_respuestas_sociales()
    # «Buenas noches» se trata siempre como saludo, sin inventar memoria de sesión.
    for categoria, respuesta in (("saludos", SALUDO), ("despedidas", DESPEDIDA),
                                 ("agradecimientos", AGRADECIMIENTO)):
        if patrones[categoria].fullmatch(social):
            return respuesta
    return None


def respuesta_local(texto: str) -> str | None:
    """Sólo mensajes completos inequívocos. La consulta pendiente no se descarta."""
    social = respuesta_social(texto)
    if social is not None:
        return social
    normalizado = normalizar_comparacion(texto)
    if _CAPACIDADES.fullmatch(normalizado):
        return SALUDO
    if (_RECETA.fullmatch(normalizado) and not _CONTEXTO_INSTITUCIONAL.search(normalizado)
            and not nombres_mencionados(normalizado)):
        return ALCANCE
    return None
