"""T47: guardas locales sobre entrada sanitizada, sin logs ni servicios externos."""

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.services.sanitizador import normalizar_comparacion


MENSAJE_SEGURIDAD = "No puedo procesar esa solicitud."
MENSAJE_NETIQUETA = (
    "Para continuar, te pedimos mantener un lenguaje respetuoso y adecuado "
    "para una consulta institucional."
)

# Mantener íntegra la guarda preexistente, también dentro de citas explicativas.
_SEGURIDAD = re.compile(
    r"\b(?:mostra\w*|revela\w*|imprimi\w*|dame)\b.{0,60}"
    r"\b(?:api[ _-]?keys?|credenciales|secretos|prompt (?:del )?sistema)\b"
    r"|\bignora\w* (?:todas |las |tus )*(?:instrucciones|reglas)\b"
)

RUTA_CATALOGO = Path(__file__).resolve().parents[1] / "config" / "moderacion.json"
_CLAVES = (
    "insultos", "insultos_aislados", "groserias_contextuales", "amenazas_frases",
    "discriminacion_frases", "incitaciones_discriminatorias", "terminos_grupo",
)
# Respaldo deliberadamente pequeño y conocido, no una copia del catálogo editable.
_RESPALDO = {
    "insultos": ("idiota", "imbecil", "pelotudo"),
    "insultos_aislados": ("idiota", "imbecil", "pelotudo"),
    "groserias_contextuales": ("mierda",),
    "amenazas_frases": ("te voy a matar",),
    "discriminacion_frases": ("de mierda", "son inferiores"),
    "incitaciones_discriminatorias": ("hay que exterminar a",),
    "terminos_grupo": ("negro", "inmigrante"),
}
_NEGACION_PREVIA = re.compile(r"\b(?:no|nunca|jamas)\s+$")

# Excepción acotada a una pregunta explicativa completa con una sola cita.
# No basta incluir «estoy estudiando», ni agregar una agresión fuera de la cita.
_EXPLICACION_CITADA = re.compile(
    r"^¿?(?:que significa|que quiere decir|como se interpreta|explicame)\s+"
    r"(?:(?:la palabra|la frase|la expresion|el termino)\s+)?"
    r"(?:\"[^\"“”«»']+\"|“[^\"“”«»']+”|«[^\"“”«»']+»|'[^\"“”«»']+')"
    r"(?:\s+(?:en este contexto|en un texto|en un contexto academico|"
    r"en analisis del discurso))?[?.!]*$"
)


@dataclass(frozen=True)
class CatalogoModeracion:
    insultos: tuple[str, ...]
    insultos_aislados: tuple[str, ...]
    groserias_contextuales: tuple[str, ...]
    amenazas_frases: tuple[str, ...]
    discriminacion_frases: tuple[str, ...]
    incitaciones_discriminatorias: tuple[str, ...]
    terminos_grupo: tuple[str, ...]
    patrones: tuple[re.Pattern[str], ...] = field(repr=False)


class _CatalogoInvalido(ValueError):
    """Error de datos locales; nunca incorpora el contenido recibido."""


def _validar_catalogo(datos: object) -> dict[str, tuple[str, ...]]:
    if not isinstance(datos, dict) or set(datos) != set(_CLAVES):
        raise _CatalogoInvalido("Estructura de catálogo inválida.")
    vocabulario = {}
    palabras = {"insultos", "insultos_aislados", "groserias_contextuales", "terminos_grupo"}
    for clave in _CLAVES:
        valores = datos[clave]
        if not isinstance(valores, list) or not valores:
            raise _CatalogoInvalido("Se requieren listas no vacías.")
        normalizados = []
        for valor in valores:
            if not isinstance(valor, str):
                raise _CatalogoInvalido("Se requieren textos de vocabulario.")
            limpio = normalizar_comparacion(valor)
            if (not limpio or not all(palabra.isalpha() for palabra in limpio.split())
                    or (clave in palabras and " " in limpio)):
                raise _CatalogoInvalido("Se requieren palabras o frases sin puntuación ni regex.")
            normalizados.append(limpio)
        vocabulario[clave] = tuple(dict.fromkeys(normalizados))
    if not set(vocabulario["insultos_aislados"]).issubset(vocabulario["insultos"]):
        raise _CatalogoInvalido("El subconjunto de insultos aislados es inválido.")
    return vocabulario


def _variantes(palabra: str) -> set[str]:
    formas = {palabra}
    if palabra.endswith("o"):
        formas.add(palabra[:-1] + "a")
    for forma in tuple(formas):
        if not forma.endswith("s"):
            formas.add(forma + ("s" if forma.endswith(tuple("aeiouy")) else "es"))
    return formas


def _alternativas(valores, *, variantes=False) -> str:
    formas = {forma for valor in valores
              for forma in (_variantes(valor) if variantes else {valor})}
    ordenadas = sorted(formas, key=lambda v: (-len(v), v))
    return "(?:" + "|".join(re.escape(v) for v in ordenadas) + ")"


def _construir_catalogo(vocabulario: dict[str, tuple[str, ...]]) -> CatalogoModeracion:
    insultos = _alternativas(vocabulario["insultos"], variantes=True)
    aislados = _alternativas(vocabulario["insultos_aislados"], variantes=True)
    groserias = _alternativas(vocabulario["groserias_contextuales"])
    grupos = _alternativas(vocabulario["terminos_grupo"], variantes=True)
    degradantes = _alternativas(vocabulario["discriminacion_frases"])
    incitaciones = _alternativas(vocabulario["incitaciones_discriminatorias"])
    amenazas = set(vocabulario["amenazas_frases"])
    for frase in vocabulario["amenazas_frases"]:
        if frase.startswith("te voy a "):
            accion = frase.removeprefix("te voy a ")
            verbo, separador, resto = accion.partition(" ")
            if verbo.endswith(("ar", "er", "ir")):
                amenazas.add(f"voy a {verbo}te{separador}{resto}")
                for enlace in ("encontrar", "buscar"):
                    amenazas.add(f"te voy a {enlace} y {accion}")
    patrones = (
        rf"\b(?:sos|eres|usted es|ustedes son)\s+"
        rf"(?:(?:un|una|unos|unas|muy|re)\s+)?{insultos}\b",
        rf"\bque\s+{insultos}\s+que\s+(?:sos|eres|son)\b",
        rf"\b(?:anda(?:te)?|vete)\s+(?:a la|al)\s+{groserias}\b",
        rf"(?:^|\baclaracion:\s*)[¡¿]?{aislados}\b[.!?¡¿]*\s*(?=$|\baclaracion:)",
        rf"\b{_alternativas(amenazas)}\b",
        rf"\b(?:(?:sos|eres|usted es)\s+(?:un|una)|los|las)\s+{grupos}\s+{degradantes}\b",
        rf"\b{incitaciones}\s+(?:(?:todos|todas)\s+)?(?:los|las)\s+{grupos}\b",
    )
    return CatalogoModeracion(**vocabulario, patrones=tuple(re.compile(p) for p in patrones))


@lru_cache(maxsize=8)
def cargar_catalogo(ruta_config: Path | None = None) -> CatalogoModeracion:
    """Carga/compila una vez por ruta. Reiniciar tras editar, incluso si hubo respaldo.

    Cualquier error de archivo o validación activa todo el respaldo mínimo. No se
    mezclan entradas válidas con inválidas ni se registra contenido del catálogo.
    """
    ruta = RUTA_CATALOGO if ruta_config is None else Path(ruta_config)
    try:
        vocabulario = _validar_catalogo(json.loads(ruta.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError, _CatalogoInvalido):
        vocabulario = _RESPALDO
    return _construir_catalogo(vocabulario)


def _hay_agresion(normalizado: str, catalogo: CatalogoModeracion) -> bool:
    if _EXPLICACION_CITADA.fullmatch(normalizado):
        return False
    for patron in catalogo.patrones:
        for coincidencia in patron.finditer(normalizado):
            # La negación sólo excluye su propia coincidencia, nunca otra agresión.
            antes = normalizado[max(0, coincidencia.start() - 12):coincidencia.start()]
            if not _NEGACION_PREVIA.search(antes):
                return True
    return False


def entrada_bloqueada(texto: str, *, ruta_config: Path | None = None) -> bool:
    """Evalúa la consulta combinada completa; no registra contenido ni categorías."""
    normalizado = normalizar_comparacion(texto)
    return bool(_SEGURIDAD.search(normalizado)
                or _hay_agresion(normalizado, cargar_catalogo(ruta_config)))


def mensaje_bloqueo(texto: str) -> str:
    """Selecciona el mensaje fijo sólo después de confirmar un bloqueo local."""
    if _SEGURIDAD.search(normalizar_comparacion(texto)):
        return MENSAJE_SEGURIDAD
    return MENSAJE_NETIQUETA
