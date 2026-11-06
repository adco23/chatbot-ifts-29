"""Objetivos semánticos acotados y proyecciones sin cambiar los fragmentos."""

import pytest

from app.services.objetivo_consulta import (
    contenido_pertinente, faq_compatible, identificar_objetivo, respuesta_compatible,
)


@pytest.mark.parametrize("consulta,objetivo", [
    ("¿Cuáles son las materias del primer cuatrimestre?", "plan_estudios"),
    ("¿Qué materias tengo al principio de la cursada?", "plan_estudios"),
    ("¿Dónde veo las materias en las que estoy inscripto?", "matriculacion"),
    ("¿Dónde aparecen mis materias en Moodle?", "matriculacion"),
    ("¿Cómo solicito una constancia de alumno regular?", "procedimiento_regular"),
    ("¿Qué significa ser alumno regular?", "definicion_regular"),
    ("Quiero el certificado de asistencia a un examen parcial", "procedimiento_asistencia"),
    ("¿Cómo entrego el certificado analítico?", "procedimiento_analitico"),
    ("Tengo un trámite administrativo que no encuentro", "tramite_administrativo"),
    ("¿Con cuánto promociono una materia?", "promocion"),
    ("Cronograma de Análisis Matemático", "cronograma"),
    ("Necesito información sobre mis materias", None),
])
def test_objetivos_sin_clasificador_llm(consulta, objetivo):
    assert identificar_objetivo(consulta) == objetivo


def test_bloque_definicion_no_responde_procedimiento():
    original = ("10. ¿Qué significa ser alumno regular? Ser alumno regular implica aprobar. "
                "11. ¿Cómo solicito una constancia de alumno regular? "
                "El certificado de alumno regular se solicita en SIU Guaraní.")
    procedimiento = contenido_pertinente(original, "procedimiento_regular")
    assert "se solicita en SIU" in procedimiento and "implica aprobar" not in procedimiento
    assert "implica aprobar" in original
    assert contenido_pertinente("Ser alumno regular implica aprobar.", "procedimiento_regular") == ""


@pytest.mark.parametrize("consulta,canonica,respuesta", [
    ("Cómo solicito una constancia de alumno regular?", "Qué significa ser alumno regular?", "Ser alumno regular implica aprobar."),
    ("Qué materias tengo al principio de la cursada?", "Dónde veo mis materias en Moodle?", "Están en Moodle."),
])
def test_faq_de_otro_objetivo_es_miss(consulta, canonica, respuesta):
    assert not faq_compatible(consulta, canonica, respuesta)


def test_inscripcion_a_promociones_no_es_regimen_de_promocion():
    assert contenido_pertinente("Las inscripciones a promociones se gestionan en SIU.", "promocion") == ""
    assert contenido_pertinente("Para la promoción se requiere una nota mínima de 7.", "promocion")


def test_respuesta_de_definicion_no_satisface_tramite():
    assert not respuesta_compatible("Ser alumno regular significa aprobar.", "procedimiento_regular")
    assert respuesta_compatible("Podés solicitar la constancia en SIU.", "procedimiento_regular")
