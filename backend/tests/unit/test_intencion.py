from app.services.intencion_documental import requiere_materia_por_consulta

def test_consultas_generales_no_requieren_materia():
    assert requiere_materia_por_consulta("¿Cuándo empiezan las clases?") is False
    assert requiere_materia_por_consulta("¿Cuándo es la inscripción?") is False
    assert requiere_materia_por_consulta("¿Qué documentación tengo que presentar?") is False
    assert requiere_materia_por_consulta("¿Cómo ingreso a Moodle?") is False

def test_consultas_especificas_requieren_materia():
    assert requiere_materia_por_consulta("¿Cuál es el horario de Técnicas?") is True
    assert requiere_materia_por_consulta("¿Qué contenidos tiene Técnicas?") is True