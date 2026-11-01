"""Intención documental determinista, sin proveedores ni llamadas externas."""

import re

from app.models.rag import TipoDocumental
from app.services.sanitizador import normalizar_comparacion
from app.settings import Configuracion

# Palabras clave generales que NUNCA deben requerir materia/comisión
GENERAL_KEYWORDS = [
    "inicio de clases", "empiezan las clases", "comienzo de clases",
    "inscripción", "inscribir", "preinscripción", "preinscribir",
    "documentación", "ingresantes", "ingresante",
    "moodle", "siu", "guaraní",
    "regularidad", "asistencia",
    "contacto", "bedelía", "bedelia",
    "comisión", "comision",
    "calendario académico", "calendario academico", "fechas institucionales", "fechas"
]

# Palabras clave académicas específicas que SÍ requieren contexto de materia
ACADEMIC_SPECIFIC_KEYWORDS = [
    "contenido", "contenidos", "programa",
    "horario", "horarios",
    "parcial", "parciales",
    "recuperatorio", "recuperatorios",
    "docente", "docentes", "profesor", "profesores",
    "cronograma"
]

def requiere_materia_por_consulta(texto: str) -> bool:
    texto_lower = texto.lower()
    # Si la consulta contiene términos institucionales generales, NO requiere materia
    for kw in GENERAL_KEYWORDS:
        if kw in texto_lower:
            return False

    # Si contiene términos específicos de una materia (horarios, contenidos, parciales, etc.)
    for kw in ACADEMIC_SPECIFIC_KEYWORDS:
        if kw in texto_lower:
            return True
    return False

def clasificar_intencion_documental(texto: str, config: Configuracion) -> TipoDocumental | None:
    consulta = normalizar_comparacion(texto)
    for tipo in ("cronograma", "programa", "general"):
        for termino in getattr(config, f"rag_terminos_{tipo}"):
            patron = r"(?<!\w)" + re.escape(normalizar_comparacion(termino)) + r"(?!\w)"
            if re.search(patron, consulta):
                return tipo
    return None