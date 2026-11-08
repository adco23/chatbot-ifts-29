"""Objetivos y bloques pertinentes compartidos por FAQ, chat y curación; sin LLM."""

import re

from app.services.sanitizador import normalizar_comparacion, sanitizar_texto
from app.services.intencion_documental import es_inicio_materia, es_orientacion_primer_dia
from app.services.eventos_cronograma import normalizar_referencias_pfo

PROCEDIMIENTOS = frozenset({
    "procedimiento_regular", "procedimiento_asistencia", "procedimiento_analitico",
    "procedimiento_certificado", "tramite_administrativo",
})
OBJETIVOS_DIRIGIDOS = PROCEDIMIENTOS | {"plan_estudios", "matriculacion", "promocion",
                                      "definicion_regular"}
_ACCION = re.compile(
    r"\b(?:solicita\w*|solicitud|pedir\w*|pedi\w*|pide\w*|tramita\w*|tramitacion|"
    r"ingresa\w*|accede\w*|presenta\w*|entrega\w*|envia\w*|emite\w*|emitido|"
    r"canales administrativos|sistema de gestion|seccion|plataforma)\b"
)
_UNIDADES = r"(?:materias|asignaturas|unidades curriculares)"
_ALCANCE_PLAN = r"(?:carrera|cursada|tecnicatura|plan(?: de estudios)?)"
_TOTAL_PLAN = re.compile(
    rf"\b{_ALCANCE_PLAN}\s+(?:tiene|comprende|incluye|consta de|se compone de|"
    rf"esta compuest[oa] por)\s+(?:(?:un )?total de\s+)?([1-9][0-9]{{0,2}})\s+{_UNIDADES}\b"
    rf"|\btotal(?: de {_UNIDADES})?\s+(?:del|de la)\s+{_ALCANCE_PLAN}"
    rf"\s*:\s*([1-9][0-9]{{0,2}})\b"
    rf"|\btotal\s*:\s*([1-9][0-9]{{0,2}})\s+unidades curriculares\b"
)
_CANTIDAD_UNIDADES = re.compile(rf"\b([1-9][0-9]{{0,2}})\s+{_UNIDADES}\b")
_ALCANCE_PARCIAL = re.compile(
    r"\b(?:primer[oa]?|segund[oa]|tercer[oa]?|cuart[oa]|quint[oa]|sext[oa]|[1-6][°º]?)"
    r"\s+(?:ano|cuatrimestre)\b"
)


def es_consulta_total_plan(texto: str) -> bool:
    consulta = normalizar_comparacion(texto)
    return bool(
        re.search(rf"\b(?:cuantas|cantidad(?: total)? de|total de)\s+{_UNIDADES}\b", consulta)
        and re.search(rf"\b{_ALCANCE_PLAN}\b", consulta)
        and not re.search(r"\b(?:moodle|matriculad\w*|inscript\w*|asignad\w*)\b", consulta)
        and not _ALCANCE_PARCIAL.search(consulta)
    )


def _totales_plan_explicitos(texto: str) -> set[int]:
    # Sólo declaraciones de total del plan/carrera, nunca un recuento de chunks.
    oraciones = re.split(r"(?<=[.!?;])\s+|\n+", normalizar_comparacion(texto))
    return {int(match[1] or match[2] or match[3]) for oracion in oraciones
            if not _ALCANCE_PARCIAL.search(oracion)
            for match in _TOTAL_PLAN.finditer(oracion)}


def total_plan_respaldado(consulta: str, contenidos, *, respuesta: str | None = None) -> bool:
    """Guarda compartida por generación y HIT: exige un total explícito sin conflictos."""
    if not es_consulta_total_plan(consulta):
        return True
    totales = {n for contenido in contenidos for n in _totales_plan_explicitos(contenido)}
    if len(totales) != 1:
        return False
    if respuesta is None:
        return True
    mencionados = {int(m[1]) for m in _CANTIDAD_UNIDADES.finditer(normalizar_comparacion(respuesta))}
    mencionados.update(_totales_plan_explicitos(respuesta))
    return mencionados == totales


def identificar_objetivo(texto: str) -> str | None:
    consulta = normalizar_referencias_pfo(texto)
    if re.search(r"\b(?:certificados?|constancias?)\b", consulta):
        if "alumno regular" in consulta or "alumna regular" in consulta:
            return "procedimiento_regular"
        if re.search(r"\basistencia\b", consulta) and re.search(r"\b(?:examen|parcial)\b", consulta):
            return "procedimiento_asistencia"
        if "analitico" in consulta:
            return "procedimiento_analitico"
        return "procedimiento_certificado"
    if re.search(r"\btramite\s+administrativo\b", consulta):
        return "tramite_administrativo"
    if re.search(r"\b(?:promocion\w*|promociono|promocionar|promuevo)\b", consulta):
        return "promocion"
    if es_consulta_total_plan(texto):
        return "plan_estudios"
    if re.search(r"\b(?:materias|asignaturas)\b", consulta):
        if re.search(r"\b(?:moodle|matriculad\w*|inscript\w*|asignad\w*)\b", consulta):
            return "matriculacion"
        if re.search(r"\b(?:primer|primero|iniciales|principio|inicio|plan de estudios)\b"
                     r"|\b(?:debo|deberia|tengo que) cursar\b"
                     r"|\bal (?:comenzar|empezar|iniciar) (?:la )?carrera\b", consulta):
            return "plan_estudios"
    if "plan de estudios" in consulta:
        return "plan_estudios"
    if re.search(r"\b(?:que (?:significa|es)|definicion)\b.*\balumn[oa] regular\b", consulta):
        return "definicion_regular"
    if es_orientacion_primer_dia(texto):
        return None
    if (re.search(r"\b(?:cronograma|horarios?|parciales?|recuperatorios?|clases?|pfo)\b", consulta)
            or es_inicio_materia(texto)):
        return "cronograma"
    return None


def _respuesta_bloque(texto: str) -> str:
    if re.search(r"\brespuesta\s*:", texto, re.IGNORECASE):
        return re.split(r"\brespuesta\s*:", texto, maxsplit=1, flags=re.IGNORECASE)[1]
    return texto.split("?", 1)[-1]


def _pertinente(bloque: str, objetivo: str, titulo: str) -> bool:
    texto = normalizar_comparacion(bloque)
    respuesta = normalizar_comparacion(_respuesta_bloque(bloque))
    if objetivo == "plan_estudios":
        nombre = re.sub(r"[-_]", " ", normalizar_comparacion(titulo))
        curricular = bool(_totales_plan_explicitos(texto) or re.search(
            r"unidad(?:es)? curricular|denominacion|\b[1-3]\.[12]\.\d+|"
            r"(?:primer|segundo|[1-6]°?)[ ]+(?:ano|cuatrimestre)", texto))
        return "plan" in nombre and "estudios" in nombre and curricular
    if objetivo == "matriculacion":
        return "moodle" in texto and bool(re.search(r"materias|matricul|inscrip|asignad", texto))
    if objetivo == "definicion_regular":
        return bool(re.search(
            r"\balumn[oa] regular(?: en (?:el )?ifts\s*(?:n[°ºo.]*\s*)?29)? "
            r"(?:significa|implica|es)\b", respuesta))
    if objetivo == "promocion":
        return bool(re.search(r"\bpromocion\w*\b", texto) and re.search(
            r"\b(?:nota|calificacion|promedio|eximid\w*|promocionad\w*|acceder)\b", respuesta))
    if objetivo in PROCEDIMIENTOS:
        if not _ACCION.search(respuesta):
            return False
        if objetivo == "procedimiento_regular":
            return bool(re.search(r"certificad|constancia", texto) and re.search(r"alumn[oa] regular", texto))
        if objetivo == "procedimiento_asistencia":
            return bool(re.search(r"certificad|constancia", texto) and "asistencia" in texto
                        and re.search(r"\b(?:examen|parcial)\b", texto))
        if objetivo == "procedimiento_analitico":
            return "analitico" in texto
        if objetivo == "tramite_administrativo":
            return bool(re.search(r"\btramites? administrativos?\b", texto)
                        and re.search(r"pasos|se solicita|ingresar|se tramita", respuesta))
        return bool(re.search(r"certificad|constancia", texto))
    return True


def contenido_pertinente(texto: str, objetivo: str | None, titulo: str = "") -> str:
    if objetivo not in OBJETIVOS_DIRIGIDOS:
        return texto
    # Los encabezados delimitan trámites independientes antes de colapsar espacios.
    tramites = re.split(r"(?=^[ \t]*(?:#{1,6}\s*)?(?:\*\*)?Trámite\s*:)", texto,
                       flags=re.IGNORECASE | re.MULTILINE)
    if len(tramites) > 1:
        seleccionados = []
        for bloque in tramites:
            encabezado = re.match(
                r"\s*(?:#{1,6}\s*)?(?:\*\*)?Trámite\s*:\s*([^\n]+)", bloque,
                flags=re.IGNORECASE,
            )
            if encabezado and objetivo in PROCEDIMIENTOS:
                # El asunto del encabezado manda sobre menciones incidentales del cuerpo.
                asunto = identificar_objetivo(encabezado[1])
                if asunto != objetivo:
                    continue
            limpio = sanitizar_texto(bloque)
            if limpio and _pertinente(limpio, objetivo, titulo):
                seleccionados.append(limpio)
        return "\n".join(seleccionados)
    limpio = sanitizar_texto(texto)
    if objetivo == "promocion":
        # Comparar sólo condiciones, no horarios u otros apartados del programa.
        oraciones = re.split(r"(?<=[.!?])\s+", limpio)
        return "\n".join(o for o in oraciones if _pertinente(o, objetivo, titulo))
    bloques = re.split(r"(?=\b[0-9]{1,2}\.\s*¿)|(?=\bPregunta\s*:)", limpio,
                      flags=re.IGNORECASE)
    return "\n".join(b.strip() for b in bloques if b.strip() and _pertinente(b, objetivo, titulo))


def respuesta_compatible(texto: str, objetivo: str | None) -> bool:
    normal = normalizar_comparacion(texto)
    if objetivo in PROCEDIMIENTOS:
        return bool(_ACCION.search(normal)) and not bool(re.match(
            r"ser alumn[oa] regular (?:significa|implica)", normal))
    if objetivo == "plan_estudios":
        return not bool(re.search(r"\bmoodle|materias (?:asignadas|matriculadas)\b", normal))
    if objetivo == "matriculacion":
        return bool(re.search(r"\b(?:moodle|plataforma|panel|aula virtual)\b", normal))
    if objetivo == "promocion":
        return bool(re.search(r"\b(?:nota|calificacion|promedio|aprob\w*|promocion\w*|[1-9])\b", normal))
    if objetivo == "definicion_regular":
        return "regular" in normal and bool(re.search(r"asistencia|aprob|evaluaci|cursada|requisitos", normal))
    return True


def faq_compatible(consulta: str, canonica: str, respuesta: str) -> bool:
    objetivo = identificar_objetivo(consulta)
    otro = identificar_objetivo(canonica)
    return (objetivo not in OBJETIVOS_DIRIGIDOS or otro in (None, objetivo)) and respuesta_compatible(
        respuesta, objetivo,
    )


def consulta_dirigida(texto: str, objetivo: str) -> str:
    if objetivo == "plan_estudios":
        if es_consulta_total_plan(texto):
            return ("Plan de estudios completo: cantidad total de materias "
                    "y unidades curriculares de la carrera.")
        alcance = "primer año" if re.search(r"primer\s+ano", normalizar_comparacion(texto)) else "primer cuatrimestre"
        return f"Plan de estudios: materias y unidades curriculares del {alcance}."
    return {
        "procedimiento_regular": "Cómo solicitar certificado o constancia de alumno regular: procedimiento y canal administrativo.",
        "procedimiento_asistencia": "Cómo solicitar certificado de asistencia a examen parcial: procedimiento.",
        "procedimiento_analitico": "Cómo entregar el certificado analítico: procedimiento y área responsable.",
        "procedimiento_certificado": "Cómo solicitar certificados y constancias: procedimiento administrativo.",
        "tramite_administrativo": "Trámites administrativos: cómo solicitarlos y canal de atención.",
        "promocion": "Condiciones de promoción: calificaciones y requisitos para promocionar.",
        "matriculacion": "Dónde ver las materias matriculadas en la plataforma Moodle.",
        "definicion_regular": "Qué significa ser alumno regular: definición y requisitos.",
    }[objetivo]


def texto_abstencion(objetivo: str | None) -> str:
    if objetivo == "promocion":
        return "No encontré información suficiente y vigente sobre las condiciones de promoción en la documentación disponible."
    nombres = {
        "procedimiento_regular": "solicitar la constancia de alumno regular",
        "procedimiento_asistencia": "solicitar el certificado de asistencia a examen parcial",
        "procedimiento_analitico": "realizar la entrega del certificado analítico",
    }
    if objetivo in nombres:
        return f"No encontré un procedimiento específico para {nombres[objetivo]} en la documentación disponible."
    if objetivo in PROCEDIMIENTOS:
        return "No encontré un procedimiento específico para realizar ese trámite en la documentación disponible."
    return "No encontré información suficiente sobre esa consulta en la documentación disponible."
