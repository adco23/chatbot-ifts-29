"""Regla temporal común, independiente del origen FAQ o RAG de la respuesta."""

import pytest

from app.services.temporalidad import RECORDATORIO, advertencia_temporal


@pytest.mark.parametrize("consulta,tipo", [
    ("¿Cuándo abre la inscripción?", "general"),
    ("Primer parcial", None), ("Parciales de la materia", None),
    ("Recuperatorio", None), ("Recuperatorios", None),
    ("Horarios de cursada", "general"), ("Fechas académicas", None),
    ("Detalles de la organización", "cronograma"),
])
def test_consulta_temporal_o_cronograma_lleva_advertencia(consulta, tipo):
    assert advertencia_temporal(consulta, tipo_documental=tipo) == RECORDATORIO


@pytest.mark.parametrize("consulta,tipo", [
    ("Contenidos del programa", "programa"),
    ("Descripción de la materia", "programa"),
    ("Plan de estudios", "general"), ("Título de la carrera", None),
])
def test_consulta_no_temporal_no_lleva_advertencia(consulta, tipo):
    assert advertencia_temporal(consulta, tipo_documental=tipo) is None
