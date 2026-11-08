"""Pruebas de CORS: preflight del widget, orígenes permitidos y configuración."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.settings import Configuracion

WIDGET = "https://widget.example.vercel.app"
PREFLIGHT_CHAT = {
    "Access-Control-Request-Method": "POST",
    "Access-Control-Request-Headers": "content-type,x-sesion-id",
}


def _cliente(monkeypatch, origenes: str | None = WIDGET) -> TestClient:
    # app.main:app se arma al importar el módulo: hay que crear otra después de setenv.
    if origenes is not None:
        monkeypatch.setenv("CORS_ORIGENES", origenes)
    from app.main import crear_app

    return TestClient(crear_app())


def test_preflight_desde_el_widget_es_aceptado(monkeypatch):
    respuesta = _cliente(monkeypatch).options(
        "/chat", headers={"Origin": WIDGET, **PREFLIGHT_CHAT}
    )

    assert respuesta.status_code == 200
    assert respuesta.headers["access-control-allow-origin"] == WIDGET
    assert "x-sesion-id" in respuesta.headers["access-control-allow-headers"].lower()
    assert "POST" in respuesta.headers["access-control-allow-methods"]


def test_preflight_desde_otro_origen_es_rechazado(monkeypatch):
    respuesta = _cliente(monkeypatch).options(
        "/chat", headers={"Origin": "https://otro.example", **PREFLIGHT_CHAT}
    )

    assert respuesta.status_code == 400
    assert "access-control-allow-origin" not in respuesta.headers


def test_preflight_con_metodo_no_permitido_es_rechazado(monkeypatch):
    respuesta = _cliente(monkeypatch).options(
        "/chat", headers={"Origin": WIDGET, "Access-Control-Request-Method": "GET"}
    )

    assert respuesta.status_code == 400


def test_preflight_con_authorization_es_rechazado(monkeypatch):
    respuesta = _cliente(monkeypatch).options(
        "/admin/sync",
        headers={
            "Origin": WIDGET,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert respuesta.status_code == 400


def test_respuesta_de_error_lleva_encabezados_cors(monkeypatch):
    # Sin X-Sesion-Id la API responde 400 antes de llegar a los proveedores.
    respuesta = _cliente(monkeypatch).post(
        "/chat", headers={"Origin": WIDGET}, json={"mensaje": "Hola"}
    )

    assert respuesta.status_code == 400
    assert respuesta.headers["access-control-allow-origin"] == WIDGET
    assert "retry-after" in respuesta.headers["access-control-expose-headers"].lower()


def test_por_defecto_se_permite_vite_en_local(monkeypatch):
    respuesta = _cliente(monkeypatch, origenes=None).options(
        "/chat", headers={"Origin": "http://localhost:5173", **PREFLIGHT_CHAT}
    )

    assert respuesta.status_code == 200
    assert respuesta.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_configuracion_normaliza_la_lista(monkeypatch):
    monkeypatch.setenv(
        "CORS_ORIGENES", f" {WIDGET}/ , http://localhost:5173,, {WIDGET} "
    )

    assert Configuracion(_env_file=None).cors_origenes == [WIDGET, "http://localhost:5173"]


def test_configuracion_vacia_no_permite_ningun_origen(monkeypatch):
    monkeypatch.setenv("CORS_ORIGENES", "")

    assert Configuracion(_env_file=None).cors_origenes == []


@pytest.mark.parametrize("valor", ["*", "campus.example.edu.ar", f"{WIDGET},*"])
def test_configuracion_rechaza_comodin_y_origenes_sin_esquema(monkeypatch, valor):
    monkeypatch.setenv("CORS_ORIGENES", valor)

    with pytest.raises(ValidationError, match="CORS_ORIGENES"):
        Configuracion(_env_file=None)
