"""Pregunta de primer parcial con RAG simulado, providers HTTP simulados y chat real."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.providers.base import RespuestaLLM
from app.providers.dependencias import obtener_gestor_proveedores
from app.providers.gemini import ProveedorGemini
from app.providers.gestor import GestorProveedores
from app.providers.groq import ProveedorGroq
from app.services import conversacion
from tests.unit.services.test_retrieval_academico import (
    entorno_academico as entorno_academico, fragmento, http_academico as http_academico, preguntar,
)

PREGUNTA = "Cuando es el primer parcial de Tecnicas de Programacion comision F?"


def salida(**cambios):
    return json.dumps(dict(respuesta="El primer parcial es el 30 de septiembre.",
                           fragmento_ids=[101]) | cambios)


@pytest.fixture
def parcial(http_academico, entorno_academico):
    entorno_academico.filas[:] = [
        fragmento(101, documento_id=20, contenido="Semana 8. Inicia el 30/9. Primer parcial.",
                  titulo="Cronograma Técnicas F"),
        fragmento(3, "general", None, materia_id=None, contenido="Información institucional."),
    ]
    entorno_academico.llm.generar_con_respaldo.return_value = RespuestaLLM(salida(), "groq", "prueba")
    return SimpleNamespace(http=http_academico, entorno=entorno_academico)


@pytest.mark.parametrize("envolver", [False, True])
def test_groq_json_primario_pasa_y_respuesta_final_validada(parcial, caplog, envolver):
    texto = salida()
    if envolver:
        texto = f"```json\n{texto}\n```"
    parcial.entorno.llm.generar_con_respaldo.return_value = RespuestaLLM(texto, "groq", "prueba")
    respuesta = preguntar(parcial.http, PREGUNTA)
    assert respuesta.status_code == 200 and respuesta.json()["estado"] == "respondida"
    assert respuesta.json()["fuentes"][0]["documento_id"] == 20
    assert respuesta.json()["fuentes"][0]["fragmento_ids"] == [101]
    assert respuesta.json()["advertencia_temporal"]
    assert "respuesta_final_validada" in caplog.text and "proveedor=groq" in caplog.text
    assert "30 de septiembre" not in caplog.text and PREGUNTA not in caplog.text


@pytest.mark.parametrize("texto,etapa", [
    (salida(respuesta="Información institucional.", fragmento_ids=[3]), "evidencia_primaria_faltante"),
    (json.dumps({"respuesta": "El primer parcial es el 30/9."}), "schema_respuesta_error"),
    ('{"respuesta":', "json_parseado_error"),
    (salida(fragmento_ids=[999]), "fragmentos_invalidos"),
    (salida(fragmento_ids=[{"fragmento_id": 101, "documento_id": 99}]), "fragmentos_invalidos"),
    ("El primer parcial es el 30/9.", "json_parseado_error"),
])
def test_salida_invalida_es_503_controlado_con_diagnostico(parcial, caplog, texto, etapa):
    parcial.entorno.llm.generar_con_respaldo.return_value = RespuestaLLM(texto, "groq", "prueba")
    respuesta = preguntar(parcial.http, PREGUNTA)
    assert respuesta.status_code == 503
    assert respuesta.json()["detail"]["codigo"] == "servicio_no_disponible"
    assert etapa in caplog.text and "respuesta_final_validada" not in caplog.text
    assert PREGUNTA not in caplog.text and "El primer parcial" not in caplog.text


def test_gemini_falla_groq_valido_y_validacion_final_200(parcial, caplog):
    llamadas = []

    def manejar(request):
        nombre = "gemini" if request.url.host == "generativelanguage.googleapis.com" else "groq"
        llamadas.append(nombre)
        if nombre == "gemini":
            return httpx.Response(503)
        return httpx.Response(200, json={"choices": [{"message": {"content": f"```json\n{salida()}\n```"}}]})

    cliente = httpx.AsyncClient(transport=httpx.MockTransport(manejar))
    gestor = GestorProveedores([
        ProveedorGemini("ficticia", "prueba", 1, cliente=cliente),
        ProveedorGroq("ficticia", "prueba", 1, cliente=cliente),
    ], 3, backoff_segundos=0)
    parcial.http.app.dependency_overrides[obtener_gestor_proveedores] = lambda: gestor
    try:
        respuesta = preguntar(parcial.http, PREGUNTA)
    finally:
        asyncio.run(cliente.aclose())
    assert respuesta.status_code == 200 and respuesta.json()["estado"] == "respondida"
    assert llamadas == ["gemini", "gemini", "groq"]
    assert "Groq respondió" in caplog.text and "respuesta_final_validada" in caplog.text


def test_fallo_de_fuentes_no_expone_excepcion(parcial, monkeypatch, caplog):
    privado = "CONTENIDO_PRIVADO"
    monkeypatch.setattr(conversacion, "resolver_fuentes", AsyncMock(side_effect=RuntimeError(privado)))
    respuesta = preguntar(parcial.http, PREGUNTA)
    assert respuesta.status_code == 503 and "fuentes_construccion_error" in caplog.text
    assert privado not in caplog.text and privado not in respuesta.text


def test_respuesta_final_invalida_se_identifica_sin_payload(parcial, monkeypatch, caplog):
    monkeypatch.setattr(conversacion, "resolver_fuentes", AsyncMock(return_value=[{"dato": "CONTENIDO_PRIVADO"}]))
    respuesta = preguntar(parcial.http, PREGUNTA)
    assert respuesta.status_code == 503 and "respuesta_final_error" in caplog.text
    assert "CONTENIDO_PRIVADO" not in caplog.text and "CONTENIDO_PRIVADO" not in respuesta.text
