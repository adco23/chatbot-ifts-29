"""Intención documental determinista, sin proveedores ni llamadas externas."""

import re

from app.models.rag import TipoDocumental
from app.services.sanitizador import normalizar_comparacion
from app.settings import Configuracion


def es_consulta_administrativa(texto: str) -> bool:
    """El objetivo de un trámite prima sobre menciones incidentales a exámenes."""
    consulta = normalizar_comparacion(texto)
    if re.search(r"\b(?:certificados?|constancias?|tramites?)\b", consulta):
        return True
    return bool(re.search(r"\b(?:bedelia|secretaria(?: academica)?)\b", consulta)) and not bool(
        re.search(r"\b(?:cronogramas?|programas?|contenidos?|parcial(?:es)?|examen(?:es)?)\b",
                  consulta)
    )


def es_consulta_institucional(texto: str) -> bool:
    """Alcance general: trámites, listado de materias o inicio/fin del ciclo."""
    consulta = normalizar_comparacion(texto)
    return es_consulta_administrativa(texto) or bool(re.search(
        r"\b(?:inicio|fin|comienzo|comienz\w*|comenz\w*|empez\w*|empiez\w*|"
        r"inici\w*|termin\w*)\b.{0,40}\b(?:clases|ciclo lectivo|cuatrimestre)\b"
        r"|\b(?:que|cuales son|listado de|lista de)\s+(?:las )?(?:materias|asignaturas)\b"
        r"|\bplan de estudios\b",
        consulta,
    ))


def requiere_materia_por_consulta(texto: str) -> bool:
    """Compatibilidad: utiliza el único clasificador y una regla de alcance."""
    return (not es_consulta_institucional(texto)
            and clasificar_intencion_documental(texto, Configuracion())
            in ("programa", "cronograma"))


def clasificar_intencion_documental(texto: str, config: Configuracion) -> TipoDocumental | None:
    consulta = normalizar_comparacion(texto)
    for tipo in ("cronograma", "programa", "general"):
        for termino in getattr(config, f"rag_terminos_{tipo}"):
            patron = r"(?<!\w)" + re.escape(normalizar_comparacion(termino)) + r"(?!\w)"
            if re.search(patron, consulta):
                return tipo
    return None
