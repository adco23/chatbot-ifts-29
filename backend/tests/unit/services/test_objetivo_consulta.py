"""Objetivos semánticos acotados y proyecciones sin cambiar los fragmentos."""

import pytest

from app.services.objetivo_consulta import (
    consulta_dirigida, contenido_pertinente, es_consulta_total_plan, faq_compatible,
    identificar_objetivo, respuesta_compatible, total_plan_respaldado,
)


@pytest.mark.parametrize("consulta", [
    "¿Cuántas materias tiene la carrera?",
    "cuantas materias tiene la cursada",
    "¿Cuántas materias tiene toda la tecnicatura?",
    "¿Cuántas unidades curriculares tiene el plan?",
    "¿Cuántas unidades curriculares tiene la carrera?",
    "Cantidad total de asignaturas del plan de estudios",
])
def test_cantidad_total_se_dirige_al_plan_completo(consulta):
    assert identificar_objetivo(consulta) == "plan_estudios"
    assert es_consulta_total_plan(consulta)
    dirigida = consulta_dirigida(consulta, "plan_estudios")
    assert "total" in dirigida and "primer cuatrimestre" not in dirigida


@pytest.mark.parametrize("consulta", [
    "¿Cuáles son las materias del primer cuatrimestre?",
    "¿Cuántas materias del primer cuatrimestre tiene la carrera?",
    "¿Cuántas materias tengo matriculadas en Moodle?",
    "¿Cuántas clases tiene la cursada?",
])
def test_total_del_plan_no_se_confunde_con_listados_o_matriculacion(consulta):
    assert not es_consulta_total_plan(consulta)


@pytest.mark.parametrize("contenido,respaldado", [
    ("La carrera tiene 17 unidades curriculares.", True),
    ("El plan de estudios comprende un total de 17 materias.", True),
    ("Total de unidades curriculares del plan: 17.", True),
    ("Primer cuatrimestre: 4 materias. Segundo cuatrimestre: 5 materias.", False),
    ("Primer cuatrimestre: el plan tiene 4 materias.", False),
    ("Formato: Materia. Duración: cuatrimestral. Carga horaria: 64.", False),
])
def test_total_exige_declaracion_documental_no_recuento_parcial(contenido, respaldado):
    assert total_plan_respaldado("¿Cuántas materias tiene la carrera?", [contenido]) is respaldado


def test_total_conflictivo_o_respuesta_con_otro_numero_se_rechaza():
    consulta = "¿Cuántas materias tiene la carrera?"
    texto = "La carrera tiene 17 unidades curriculares."
    assert not total_plan_respaldado(consulta, [texto, "La carrera tiene 19 materias."])
    assert total_plan_respaldado(consulta, [texto], respuesta="Son 17 unidades curriculares.")
    assert not total_plan_respaldado(consulta, [texto], respuesta="Son 6 materias.")
    assert not total_plan_respaldado(consulta, [texto], respuesta="No tengo el total.")


@pytest.mark.parametrize("numero", [17, 23, 31])
def test_etiqueta_total_de_unidades_curriculares_respaldada(numero):
    texto = f"Total: {numero} unidades curriculares."
    consulta = "¿Cuántas materias tiene la carrera?"
    assert total_plan_respaldado(consulta, [texto], respuesta=f"Son {numero} unidades curriculares.")
    assert not total_plan_respaldado(consulta, [texto], respuesta="Son 6 materias.")
    assert contenido_pertinente(texto, "plan_estudios", "GENERAL__Plan-de-estudios-TSDS.docx")


@pytest.mark.parametrize("texto", [
    "Subtotal: 23 unidades curriculares.", "Total: 23 horas.", "Hay 23 fragmentos.",
    "Primer cuatrimestre: Total: 4 unidades curriculares.",
])
def test_etiqueta_total_no_acepta_subtotales_ni_otros_campos(texto):
    assert not total_plan_respaldado("¿Cuántas materias tiene la carrera?", [texto])


@pytest.mark.parametrize("consulta,objetivo", [
    ("¿Cuáles son las materias del primer cuatrimestre?", "plan_estudios"),
    ("¿Qué materias tengo al principio de la cursada?", "plan_estudios"),
    ("¿Cuáles son las cuatro materias que debo cursar?", "plan_estudios"),
    ("¿Qué materias tengo al comenzar la carrera?", "plan_estudios"),
    ("¿Qué materias curso al principio?", "plan_estudios"),
    ("¿Qué materias debo cursar en Moodle?", "matriculacion"),
    ("¿Cuándo son las PFO?", "cronograma"),
    ("¿Cuándo empieza bases de datos?", "cronograma"),
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
    ("¿Qué tengo que hacer el primer día de clases?", None),
    ("¿Qué debo llevar el primer día de clases?", None),
    ("¿Cuándo es la primera clase de Base de Datos de la comisión A?", "cronograma"),
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


@pytest.mark.parametrize("institucion", ["IFTS 29", "IFTS29", "IFTS N° 29"])
def test_definicion_admite_calificador_institucional(institucion):
    texto = f"Ser alumno regular en el {institucion} implica cumplir los requisitos de asistencia."
    assert contenido_pertinente(texto, "definicion_regular") == texto
    assert contenido_pertinente(texto, "procedimiento_regular") == ""


@pytest.mark.parametrize("prefijo", ["Trámite:", "### Trámite:", "### **Trámite:"])
def test_tramites_independientes_no_arrastran_menciones_incidentales(prefijo):
    texto = (f"{prefijo} Constancia de alumno regular\n"
             "Se solicita en el sistema SIU.\n---\n"
             f"{prefijo} Certificado analítico\n"
             "Se entrega en Secretaría; este trámite es distinto de la constancia de alumno regular.\n")
    regular = contenido_pertinente(texto, "procedimiento_regular")
    analitico = contenido_pertinente(texto, "procedimiento_analitico")
    assert "sistema SIU" in regular and "Secretaría" not in regular
    assert "Secretaría" in analitico and "sistema SIU" not in analitico
    assert "Certificado analítico" in texto  # El original se conserva.
