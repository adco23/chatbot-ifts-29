"""POST /chat T40 con repositorio real y cliente Supabase simulado, sin escrituras reales."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from postgrest.exceptions import APIError

from app.db import derivaciones
from tests.unit.services.test_cache_faq import coincidencia
from tests.unit.test_chat_faq import entorno as entorno, http as http, preguntar


@pytest.fixture
def base(monkeypatch):
    cliente = MagicMock()
    cliente.schema.return_value = cliente
    cliente.rpc.return_value = cliente
    cliente.retry.return_value = cliente
    cliente.execute.return_value = SimpleNamespace(data=True)
    monkeypatch.setattr(derivaciones, "obtener_cliente_supabase", lambda: cliente)
    return cliente


@pytest.mark.parametrize("consulta,sector", [
    ("Necesito un certificado de alumno regular", "Bedelía"),
    ("Quiero tramitar equivalencias", "Secretaría Académica"),
])
def test_http_registra_sector_y_preserva_contrato_t39(http, entorno, base, consulta, sector):
    entorno.filas[:] = []
    r = preguntar(http, consulta)
    assert r.status_code == 200 and r.json()["estado"] == "derivada"
    assert r.json()["derivacion"]["sector"] == sector
    correo = r.json()["correo_prearmado"]
    assert correo["destinatario"] == r.json()["derivacion"]["correo"]
    assert correo["asunto"] == "Consulta de estudiante - IFTS 29"
    assert correo["cuerpo"].count("\n") == 4
    base.rpc.assert_called_once_with("registrar_derivacion_anonima", {"p_sector": sector})
    base.retry.assert_called_once_with(False)
    base.execute.assert_called_once_with()


def test_http_no_envia_datos_ni_identificador_de_sesion(http, entorno, base):
    entorno.filas[:] = []
    r = preguntar(http, "Soy Juan, DNI 12345678, teléfono 5555-0101, alumno@example.org, Bedelía", {
        "consulta_pendiente": "Inscripción de Juan, DNI 12345678",
        "materia_id": 47, "comision": "F", "periodo": "2026C2",
    })
    assert r.status_code == 200 and r.json()["estado"] == "derivada"
    base.rpc.assert_called_once_with("registrar_derivacion_anonima", {"p_sector": "Bedelía"})
    base.table.assert_not_called()


@pytest.mark.parametrize("error", [
    APIError({"message": "PRIVADO", "code": "XX000", "details": None, "hint": None}),
    httpx.ReadTimeout("PRIVADO"), httpx.ConnectError("PRIVADO"),
])
def test_fallo_supabase_no_modifica_http_200_derivada(http, entorno, base, error, caplog):
    entorno.filas[:] = []
    base.execute.side_effect = error
    r = preguntar(http, "Inscripción")
    assert r.status_code == 200 and r.json()["estado"] == "derivada"
    assert r.json()["correo_prearmado"]["destinatario"] == r.json()["derivacion"]["correo"]
    base.execute.assert_called_once()
    assert type(error).__name__ in caplog.text
    assert "PRIVADO" not in caplog.text + r.text


def test_faq_hit_no_llama_rpc_de_registro(http, entorno, base):
    entorno.buscar_faq.return_value = coincidencia()
    r = preguntar(http, "¿Cuándo abre la inscripción?")
    assert r.status_code == 200 and r.json()["estado"] == "respondida"
    base.rpc.assert_not_called()
