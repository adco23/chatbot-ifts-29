import pytest

from app.services.intencion_documental import (
    es_orientacion_primer_dia, requiere_materia_por_consulta,
)

def test_consultas_generales_no_requieren_materia():
    assert requiere_materia_por_consulta("¿Cuándo empiezan las clases?") is False
    assert requiere_materia_por_consulta("¿Cuándo es la inscripción?") is False
    assert requiere_materia_por_consulta("¿Qué documentación tengo que presentar?") is False
    assert requiere_materia_por_consulta("¿Cómo ingreso a Moodle?") is False

def test_consultas_especificas_requieren_materia():
    assert requiere_materia_por_consulta("¿Cuál es el horario de Técnicas?") is True
    assert requiere_materia_por_consulta("¿Qué contenidos tiene Técnicas?") is True


@pytest.mark.parametrize("consulta,general", [
    ("¿Qué tengo que hacer el primer día de clases?", True),
    ("¿Qué debo llevar el primer día de clases?", True),
    ("Recomendaciones para el primer día de clases", True),
    ("¿Qué tengo que hacer el primer día de clases de Base de Datos?", False),
    ("¿Cuándo es la primera clase de Base de Datos de la comisión A?", False),
    ("¿Qué tengo que hacer el primer día de clases de mi materia?", False),
    ("¿Cuál es el horario del primer día de clases?", False),
])
def test_orientacion_no_generaliza_consultas_academicas(consulta, general):
    assert es_orientacion_primer_dia(consulta) is general
    assert requiere_materia_por_consulta(consulta) is not general
