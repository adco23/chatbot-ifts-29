"""Intención documental determinista, sin proveedores ni llamadas externas."""

import re

from app.models.rag import TipoDocumental
from app.services.sanitizador import normalizar_comparacion
from app.services.sinonimos import nombres_mencionados
from app.services.eventos_cronograma import normalizar_referencias_pfo
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


def es_orientacion_primer_dia(texto: str) -> bool:
    """Orientación de ingreso, sin convertir una clase académica en consulta general."""
    consulta = normalizar_comparacion(texto)
    return bool(
        re.search(r"\bprimer dia de (?:clases|cursada)\b", consulta)
        and re.search(r"\b(?:hacer|llevar|presentar|preparar\w*|orientacion|recomendaciones)\b", consulta)
        and not nombres_mencionados(texto)
        and not re.search(r"\b(?:materia|asignatura|parciales?|examenes?|pfo|cronograma)\b", consulta)
    )


def es_consulta_institucional(texto: str) -> bool:
    """Alcance general: trámites, listado de materias o inicio/fin del ciclo."""
    consulta = normalizar_comparacion(texto)
    return es_orientacion_primer_dia(texto) or es_consulta_administrativa(texto) or bool(re.search(
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


def es_inicio_materia(texto: str) -> bool:
    """Inicio de una materia explícita, sin confundirlo con una inscripción."""
    consulta = normalizar_comparacion(texto)
    return bool(
        re.search(r"\b(?:inicio|comienzo|comienz\w*|comenz\w*|empiez\w*|empez\w*)\b", consulta)
        and nombres_mencionados(texto)
        and not re.search(r"\b(?:inscrip\w*|matricula\w*|tramites?)\b", consulta)
    )


def clasificar_intencion_documental(texto: str, config: Configuracion) -> TipoDocumental | None:
    consulta = normalizar_referencias_pfo(texto)
    for tipo in ("cronograma", "programa", "general"):
        for termino in getattr(config, f"rag_terminos_{tipo}"):
            patron = r"(?<!\w)" + re.escape(normalizar_comparacion(termino)) + r"(?!\w)"
            if re.search(patron, consulta):
                return tipo
    if es_inicio_materia(texto):
        return "cronograma"
    return None
