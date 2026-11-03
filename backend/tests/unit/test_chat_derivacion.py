"""Contrato HTTP T38 con FAQ/RAG reales y proveedores externos simulados."""

from unittest.mock import Mock

import pytest

from app.services import derivacion
from tests.unit.services.test_cache_faq import coincidencia
from tests.unit.test_chat_faq import entorno as entorno, http as http, preguntar


@pytest.fixture
def buscar_sector(monkeypatch):
    buscar = Mock(wraps=derivacion.find_referral_department)
    monkeypatch.setattr(derivacion, "find_referral_department", buscar)
    return buscar


def test_sin_evidencia_http_deriva_con_contacto_tipado(http, entorno, buscar_sector):
    entorno.filas[:] = []
    r = preguntar(http, "Necesito un certificado de alumno regular")
    assert r.status_code == 200 and r.json()["estado"] == "derivada"
    assert r.json()["derivacion"] == {
        "sector": "Bedelía", "correo": "bedelia@ifts29.edu.ar",
        "horario": "Lunes a Viernes de 18:30 a 21:30", "responsable": "Equipo de Bedelía",
    }
    assert r.json()["fuentes"] == []
    buscar_sector.assert_called_once()
    entorno.llm.generar_con_respaldo.assert_not_awaited()
    assert http.get("/health").status_code == 200
    assert "/admin/sync" in http.get("/openapi.json").json()["paths"]


def test_faq_hit_http_no_deriva(http, entorno, buscar_sector):
    entorno.buscar_faq.return_value = coincidencia()
    r = preguntar(http, "¿Cuándo abre la inscripción?")
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert r.json()["derivacion"] is None
    entorno.contador.assert_called_once_with(7)
    buscar_sector.assert_not_called()


def test_rag_suficiente_http_no_deriva(http, entorno, buscar_sector):
    r = preguntar(http, "¿Cuándo abre la inscripción?")
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert r.json()["derivacion"] is None
    buscar_sector.assert_not_called()
    entorno.llm.generar_con_respaldo.assert_awaited_once()


def test_fuera_de_alcance_http_no_deriva(http, entorno):
    entorno.filas[:] = []
    r = preguntar(http, "¿Cuánto gana Messi?")
    assert r.status_code == 200 and r.json()["estado"] == "sin_evidencia"
    assert r.json()["derivacion"] is None


def test_directorio_invalido_no_rompe_chat(http, entorno, monkeypatch, tmp_path):
    entorno.filas[:] = []
    ruta = tmp_path / "derivaciones.json"
    ruta.write_text("no es JSON", encoding="utf-8")
    monkeypatch.setattr(derivacion, "DEFAULT_JSON_PATH", ruta)
    r = preguntar(http, "Inscripción")
    assert r.status_code == 200 and r.json()["estado"] == "sin_evidencia"
    assert r.json()["derivacion"] is None
