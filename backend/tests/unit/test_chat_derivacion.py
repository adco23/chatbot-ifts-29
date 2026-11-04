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


@pytest.mark.parametrize("consulta,sector,correo,horario,responsable", [
    ("Necesito un certificado de alumno regular", "Bedelía", "bedelia@ifts29.edu.ar",
     "Lunes a Viernes de 18:30 a 21:30", "Equipo de Bedelía"),
    ("Quiero tramitar equivalencias", "Secretaría Académica", "secretaria@ifts29.edu.ar",
     "Martes y Jueves de 19:00 a 21:00", "Secretaría"),
])
def test_sin_evidencia_http_deriva_con_contacto_tipado(
    http, entorno, buscar_sector, consulta, sector, correo, horario, responsable,
):
    entorno.filas[:] = []
    r = preguntar(http, consulta)
    assert r.status_code == 200 and r.json()["estado"] == "derivada"
    assert r.json()["derivacion"] == {
        "sector": sector, "correo": correo,
        "horario": horario, "responsable": responsable,
    }
    assert r.json()["correo_prearmado"] == {
        "destinatario": r.json()["derivacion"]["correo"],
        "asunto": "Consulta de estudiante - IFTS 29",
        "cuerpo": (
            "Hola,\n\n"
            "Quisiera realizar una consulta relacionada con gestiones estudiantiles del IFTS 29."
            "\n\nMuchas gracias."
        ),
    }
    assert "\\n\\n" in r.text
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
    assert r.json()["correo_prearmado"] is None
    entorno.contador.assert_called_once_with(7)
    buscar_sector.assert_not_called()


def test_rag_suficiente_http_no_deriva(http, entorno, buscar_sector):
    r = preguntar(http, "¿Cuándo abre la inscripción?")
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    assert r.json()["derivacion"] is None
    assert r.json()["correo_prearmado"] is None
    buscar_sector.assert_not_called()
    entorno.llm.generar_con_respaldo.assert_awaited_once()


def test_fuera_de_alcance_http_no_deriva(http, entorno):
    entorno.filas[:] = []
    r = preguntar(http, "¿Cuánto gana Messi?")
    assert r.status_code == 200 and r.json()["estado"] == "sin_evidencia"
    assert r.json()["derivacion"] is None
    assert r.json()["correo_prearmado"] is None


def test_directorio_invalido_no_rompe_chat(http, entorno, monkeypatch, tmp_path):
    entorno.filas[:] = []
    ruta = tmp_path / "derivaciones.json"
    ruta.write_text("no es JSON", encoding="utf-8")
    monkeypatch.setattr(derivacion, "DEFAULT_JSON_PATH", ruta)
    r = preguntar(http, "Inscripción")
    assert r.status_code == 200 and r.json()["estado"] == "sin_evidencia"
    assert r.json()["derivacion"] is None
    assert r.json()["correo_prearmado"] is None
