"""Contrato HTTP real, con dependencias de proveedores reemplazadas y sin red."""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from app.providers.base import CuotaAgotada, RespuestaLLM
from app.providers.dependencias import obtener_gestor_proveedores
from app.providers.embeddings.dependencias import obtener_gestor_embeddings
from app.providers.embeddings.base import ResultadoEmbedding
from app.providers.gestor import GestorProveedores
from app.services.limite_solicitudes import ContadorEnMemoria, obtener_contador_solicitudes
from app.services import conversacion, recuperador
from app.settings import Configuracion, obtener_configuracion
from tests.unit.services.test_conversacion import entorno as entorno
from tests.unit.services.test_conversacion import fragmento, recuperacion, salida

SESION = {"X-Sesion-Id": "11111111-1111-4111-8111-111111111111"}


@pytest.fixture
def http(entorno, configuracion_aislada):
    from app.main import crear_app

    app = crear_app()
    app.dependency_overrides[obtener_gestor_embeddings] = lambda: entorno.embeddings
    app.dependency_overrides[obtener_gestor_proveedores] = lambda: entorno.llm
    with TestClient(app) as cliente:
        yield cliente


def test_chat_real_con_mocks(http, entorno, caplog):
    respuesta = http.post("/chat", headers=SESION, json={"mensaje": "¿Cuándo abre la inscripción?"})
    assert respuesta.status_code == 200
    assert respuesta.json()["estado"] == "respondida"
    assert respuesta.json()["fuentes"][0]["titulo"] == "Calendario"
    assert "¿Cuándo abre" not in caplog.text
    assert set(http.get("/openapi.json").json()["paths"]) >= {"/chat", "/health", "/admin/sync"}


def test_chat_con_recuperador_real_y_proveedores_falsos(http, entorno, monkeypatch):
    embeddings = SimpleNamespace(embed_query=AsyncMock(return_value=ResultadoEmbedding(
        [1.0] + [0.0] * 767, "gemini", "gemini-embedding-001", 768)))
    http.app.dependency_overrides[obtener_gestor_embeddings] = lambda: embeddings
    monkeypatch.setattr(conversacion, "recuperar_fragmentos", recuperador.recuperar_fragmentos)
    buscar = Mock(return_value=[fragmento()])
    monkeypatch.setattr(recuperador.repositorio, "buscar_fragmentos_similares", buscar)
    respuesta = http.post("/chat", headers=SESION, json={"mensaje": "¿Cuándo me inscribo?"})
    assert respuesta.status_code == 200
    assert respuesta.json()["estado"] == "respondida"
    embeddings.embed_query.assert_awaited_once_with("¿Cuándo me inscribo?")
    buscar.assert_called_once()
    entorno.llm.generar_con_respaldo.assert_awaited_once()


@pytest.mark.parametrize("headers", [{}, {"X-Sesion-Id": "invalido"}])
def test_sesion_invalida(http, entorno, headers):
    assert http.post("/chat", headers=headers, json={"mensaje": "Hola"}).status_code == 400
    entorno.rag.assert_not_called()


@pytest.mark.parametrize("body", [{"mensaje": ""}, {"mensaje": 1},
                                  {"mensaje": "Hola", "otro": 1}])
def test_validacion_http(http, entorno, body):
    assert http.post("/chat", headers=SESION, json=body).status_code == 422
    entorno.rag.assert_not_called()


def test_sin_evidencia_http(http, entorno):
    entorno.rag.return_value = recuperacion([])
    respuesta = http.post("/chat", headers=SESION, json={"mensaje": "Pregunta"})
    assert respuesta.json()["estado"] == "sin_evidencia"
    entorno.llm.generar_con_respaldo.assert_not_called()


def test_error_http_controlado(http, entorno):
    entorno.rag.side_effect = RuntimeError("CLAVE_SECRETA")
    respuesta = http.post("/chat", headers=SESION, json={"mensaje": "Pregunta"})
    assert respuesta.status_code == 503
    assert "CLAVE" not in respuesta.text


def test_respaldo_llm_en_endpoint(http, entorno):
    principal = SimpleNamespace(nombre="principal", generar=AsyncMock(side_effect=CuotaAgotada()))
    respaldo = SimpleNamespace(nombre="respaldo", generar=AsyncMock(
        return_value=RespuestaLLM(salida(), "respaldo", "modelo")))
    gestor = GestorProveedores([principal, respaldo], presupuesto_segundos=9)
    http.app.dependency_overrides[obtener_gestor_proveedores] = lambda: gestor
    assert http.post("/chat", headers=SESION, json={"mensaje": "Inscripción"}).status_code == 200
    principal.generar.assert_awaited_once()
    respaldo.generar.assert_awaited_once()


def test_salida_invalida_no_se_publica(http, entorno):
    entorno.llm.generar_con_respaldo.return_value = RespuestaLLM(
        "TEXTO_NO_VALIDADO", "fake", "fake")
    respuesta = http.post("/chat", headers=SESION, json={"mensaje": "Inscripción"})
    assert respuesta.status_code == 503
    assert "TEXTO_NO_VALIDADO" not in respuesta.text


def test_import_main_aislado(tmp_path):
    ambiente = dict(os.environ)
    for campo in Configuracion.model_fields:
        ambiente.pop(campo.upper(), None)
    ambiente["ENTORNO"] = "produccion"
    ambiente["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    proceso = subprocess.run(
        [sys.executable, "-c",
         "from app.main import app; assert '/chat' in app.openapi()['paths']"],
        cwd=tmp_path, env=ambiente, capture_output=True, timeout=30,
    )
    assert proceso.returncode == 0


def test_limite_y_health_admin(http, entorno):
    reloj = [0.0]
    contador = ContadorEnMemoria(reloj=lambda: reloj[0])
    http.app.dependency_overrides[obtener_contador_solicitudes] = lambda: contador
    http.app.dependency_overrides[obtener_configuracion] = lambda: Configuracion(
        limite_solicitudes_maximo=1)
    assert http.post("/chat", headers=SESION, json={"mensaje": "Hola"}).status_code == 200
    cantidad = entorno.rag.call_count
    respuesta = http.post("/chat", headers=SESION, json={"mensaje": "Hola"})
    assert respuesta.status_code == 429 and respuesta.headers["Retry-After"] == "60"
    assert entorno.rag.call_count == cantidad
    assert http.get("/health").status_code == 200
    assert http.post("/admin/sync").status_code == 401
    reloj[0] = 61
    assert http.post("/chat", headers=SESION, json={"mensaje": "Hola"}).status_code == 200
