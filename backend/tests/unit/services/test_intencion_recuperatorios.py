"""Vocabulario de cronograma y su conexión existente con el retrieval, sin red."""

import json

import pytest

from app.providers.base import RespuestaLLM
from app.services.intencion_documental import clasificar_intencion_documental
from app.settings import Configuracion, obtener_configuracion
from tests.unit.services.test_retrieval_academico import (
    entorno_academico as entorno_academico,
    fragmento,
    http_academico as http_academico,
    preguntar,
)


@pytest.mark.parametrize("termino", [
    "recuperatorio", "recuperatorios", "RECUPERATORIOS", "parcial", "parciales",
    "examen", "exámenes", "examenes", "clase", "clases", "cronograma", "horario", "horarios",
    "fecha de parcial", "fechas de cursada",
])
def test_variantes_cronograma(termino):
    consulta = f"Cuando son los {termino} de Tecnicas de Programacion comision F?"
    assert clasificar_intencion_documental(consulta, Configuracion()) == "cronograma"


@pytest.mark.parametrize("consulta,tipo", [
    ("Que contenidos tiene Tecnicas de Programacion?", "programa"),
    ("Programa de Tecnicas de Programacion", "programa"),
    ("Objetivos y unidades de Tecnicas de Programacion", "programa"),
    ("Información institucional", "general"),
    ("Fecha de inscripción", "general"),
    ("Fechas de inscripción", "general"),
    ("Documentación para ingresar", "general"),
    ("prerrecuperatorio", None), ("recuperatorios_extra", None),
])
def test_programa_general_y_limites_de_palabra_sin_regresiones(consulta, tipo):
    assert clasificar_intencion_documental(consulta, Configuracion()) == tipo


@pytest.mark.parametrize("score,estado", [(0.668093085289001, "sin_evidencia"), (0.69, "respondida")])
def test_recuperatorios_usa_umbral_configurado_y_cronograma_f(
    http_academico, entorno_academico, monkeypatch, score, estado,
):
    monkeypatch.setenv("RAG_UMBRAL_CRONOGRAMA", "0.68")
    obtener_configuracion.cache_clear()
    titulo = "2026C2__Tecnicas-de-Programacion__COM-F__Cronograma.pdf"
    entorno_academico.filas[0] = fragmento(
        similitud=score, titulo=titulo,
        contenido="Semana 16. Recuperatorios. Inicia el 25/11.",
    )
    entorno_academico.llm.generar_con_respaldo.return_value = RespuestaLLM(
        json.dumps({"respuesta": "Los recuperatorios inician el 25 de noviembre.", "fragmento_ids": [1]}),
        "groq", "simulado",
    )
    respuesta = preguntar(http_academico, "Cuando son los recuperatorios de Tecnicas de Programacion comision F?")
    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["estado"] == estado
    assert datos["umbral"] == 0.68 and datos["confianza"] == score
    filtros = entorno_academico.consulta.call_args_list[0].kwargs["filtros"]
    assert filtros.tipo_documental == "cronograma" and filtros.comision == "F"
    assert filtros.materia_id == 47 and filtros.vigente is True
    assert entorno_academico.consulta.call_args_list[0].kwargs["modalidad"] == "primaria"
    if estado == "respondida":
        assert datos["fuentes"][0]["titulo"] == titulo
        assert datos["fuentes"][0]["fragmento_ids"] == [1]
    else:
        entorno_academico.llm.generar_con_respaldo.assert_not_called()
        assert datos["fuentes"] == []
    assert obtener_configuracion().rag_umbral_confianza == 0.70
