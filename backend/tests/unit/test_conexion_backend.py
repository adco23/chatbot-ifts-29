"""Selección local de credencial, sin red ni exposición de secretos."""

import base64
import json
from unittest.mock import Mock

import pytest

from app.db import conexion
from app.settings import obtener_configuracion

URL = "https://proyecto-ficticio.supabase.co"


def jwt_sintetico(rol):
    cuerpo = base64.urlsafe_b64encode(json.dumps({"role": rol}).encode()).decode().rstrip("=")
    return f"cabecera.{cuerpo}.firma_sintetica"


@pytest.fixture
def constructor(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", URL)
    construir = Mock(return_value=object())
    monkeypatch.setattr(conexion, "create_client", construir)
    return construir


@pytest.mark.parametrize(
    "clave", ["sb_secret_clave_backend_sintetica", jwt_sintetico("service_role")],
)
def test_prioriza_credencial_privada_y_no_persiste_sesion(monkeypatch, constructor, clave):
    monkeypatch.setenv("SUPABASE_KEY", "sb_publishable_clave_publica_sintetica")
    monkeypatch.setenv("SUPABASE_BACKEND_KEY", clave)
    assert conexion.obtener_cliente_supabase() is constructor.return_value
    constructor.assert_called_once()
    assert constructor.call_args.args == (URL, clave)
    opciones = constructor.call_args.kwargs["options"]
    assert opciones.persist_session is False and opciones.auto_refresh_token is False
    assert opciones.postgrest_client_timeout == 5.0
    assert clave not in repr(obtener_configuracion())


@pytest.mark.parametrize("clave", ["sb_publishable_no_es_privada", jwt_sintetico("anon"),
                                   jwt_sintetico("authenticated"), "cadena_invalida", "sb_secret_"])
def test_backend_invalido_no_degrada_a_clave_publica(monkeypatch, constructor, caplog, clave):
    monkeypatch.setenv("SUPABASE_KEY", "sb_publishable_alternativa")
    monkeypatch.setenv("SUPABASE_BACKEND_KEY", clave)
    assert conexion.obtener_cliente_supabase() is None
    constructor.assert_not_called()
    assert "credencial privada" in caplog.text
    assert clave not in caplog.text


def test_legacy_publica_sigue_funcionando_antes_de_0008(monkeypatch, constructor, caplog):
    clave = "sb_publishable_transicion_sintetica"
    monkeypatch.setenv("SUPABASE_KEY", clave)
    assert conexion.obtener_cliente_supabase() is constructor.return_value
    assert conexion.obtener_cliente_supabase() is constructor.return_value
    constructor.assert_called_once()
    assert caplog.text.count("credencial pública de transición") == 1
    assert clave not in caplog.text


def test_error_del_cliente_no_imprime_mensaje_remoto(monkeypatch, constructor, caplog):
    clave = "sb_secret_nunca_imprimir"
    monkeypatch.setenv("SUPABASE_BACKEND_KEY", clave)
    constructor.side_effect = RuntimeError(f"payload privado con clave {clave}")
    assert conexion.obtener_cliente_supabase() is None
    assert "RuntimeError" in caplog.text
    assert clave not in caplog.text and "payload privado" not in caplog.text


@pytest.mark.parametrize("clave", ["a.!!!.c", "a.bnVsbA.c", "a.W10.c", "", "sb_secret_"])
def test_clasificacion_local_rechaza_payloads_invalidos(clave):
    assert conexion._rol_indicado(clave) is None


def test_clave_backend_en_blanco_es_ausente(monkeypatch, constructor):
    monkeypatch.setenv("SUPABASE_BACKEND_KEY", "   ")
    monkeypatch.setenv("SUPABASE_KEY", "clave_legacy_sintetica")
    assert obtener_configuracion().supabase_backend_key is None
    assert conexion.obtener_cliente_supabase() is constructor.return_value


def test_timeout_de_transporte_es_explicito_y_configurable(monkeypatch, constructor):
    monkeypatch.setenv("SUPABASE_KEY", "clave_legacy_sintetica")
    monkeypatch.setenv("SUPABASE_TIMEOUT_SEGUNDOS", "2.5")
    assert conexion.obtener_cliente_supabase() is constructor.return_value
    assert constructor.call_args.kwargs["options"].postgrest_client_timeout == 2.5
