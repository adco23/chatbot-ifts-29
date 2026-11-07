"""Resolución explícita de materias, sin subcadenas ni IDs institucionales fijos."""

import re
from collections.abc import Sequence

from app.models.chat import MateriaChat
from app.services.sanitizador import normalizar_comparacion


MAPEO_MATERIAS = {
    "Técnicas de Programación": (
        "tecnicas", "programacion", "tec prog", "tec. de prog", "programación 1",
    ),
    "Administración de Base de Datos": (
        "administracion de bases de datos", "base de datos", "bases de datos",
        "bd", "abd", "admin de datos", "sql",
    ),
    "Elementos de Análisis Matemático": (
        "analisis", "matematica", "analisis matematico", "calculo",
    ),
    "Lógica Computacional": ("logica", "logica comp"),
}
_ALIASES = {
    normalizar_comparacion(nombre): tuple(normalizar_comparacion(alias) for alias in aliases)
    for nombre, aliases in MAPEO_MATERIAS.items()
}
_ANALISIS_MATEMATICO = normalizar_comparacion("Elementos de Análisis Matemático")
_MATE_ACADEMICO = re.compile(
    r"\b(?:materia|cronograma|horarios?|programa|contenidos|temas|cursada|parciales?|"
    r"examen(?:es)?|comision)\s+(?:de\s+)?mate\b"
)


def limpiar_texto(texto: str) -> str:
    """Compatibilidad: usa la misma normalización que el resto del chat."""
    return normalizar_comparacion(texto) if texto else ""


def _contiene_frase(texto: str, frase: str) -> bool:
    return bool(frase and re.search(r"(?<!\w)" + re.escape(frase) + r"(?!\w)", texto))


def _coincide(texto: str, nombre: str, codigo: str = "") -> bool:
    if _contiene_frase(texto, nombre):
        return True
    if any(_contiene_frase(texto, alias) for alias in _ALIASES.get(nombre, ())):
        return True
    if nombre == _ANALISIS_MATEMATICO and _MATE_ACADEMICO.search(texto):
        return True
    if not codigo:
        return False
    # Una letra o un número aislados pueden ser una comisión, fecha o cuatrimestre.
    if len(codigo) == 1 or not any(caracter.isalpha() for caracter in codigo):
        return bool(re.search(
            r"\b(?:materia|codigo(?: de materia)?)\s+" + re.escape(codigo) + r"(?!\w)", texto,
        ))
    return _contiene_frase(texto, codigo)


def nombres_mencionados(texto: str) -> tuple[str, ...]:
    """Nombres canónicos mencionados, sin asignar IDs ni resolver ambigüedades."""
    normalizado = limpiar_texto(texto)
    return tuple(nombre for nombre in MAPEO_MATERIAS
                 if _coincide(normalizado, normalizar_comparacion(nombre)))


def normalizar_y_buscar_materia(texto_usuario: str) -> str | None:
    """Wrapper compatible: nombre canónico sólo si hay una coincidencia inequívoca."""
    nombres = nombres_mencionados(texto_usuario)
    return nombres[0] if len(nombres) == 1 else None


def resolver_materia(texto: str, materias: Sequence[MateriaChat]) -> MateriaChat | None:
    """Selecciona un único ID del catálogo real; no inventa candidatos ni aliases."""
    normalizado = limpiar_texto(texto)
    candidatos = {
        materia.id: materia for materia in materias
        if _coincide(normalizado, normalizar_comparacion(materia.nombre),
                     normalizar_comparacion(materia.codigo))
    }
    return next(iter(candidatos.values())) if len(candidatos) == 1 else None
