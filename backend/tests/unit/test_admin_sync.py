"""POST y GET administrativos usan autenticación real y el mismo servicio falso."""

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.models.rag import ResumenSincronizacionDrive
from app.routers import admin
from app.services.sincronizador import SincronizacionEnCurso

TOKEN = "token-ficticio-para-test-sync"


@pytest.fixture
def http(monkeypatch, configuracion_aislada):
    monkeypatch.setenv("ADMIN_API_TOKEN", TOKEN)
    from app.main import crear_app

    with TestClient(crear_app()) as cliente:
        yield cliente


@pytest.mark.parametrize("metodo", ["GET", "POST"])
def test_autorizado_await_servicio_comun(http, monkeypatch, metodo):
    sincronizar = AsyncMock(return_value=ResumenSincronizacionDrive(nuevos=1))
    monkeypatch.setattr(admin, "sincronizar_drive_completo", sincronizar)
    respuesta = http.request(metodo, "/admin/sync", headers={"Authorization": f"Bearer {TOKEN}"})
    assert respuesta.status_code == 200
    assert respuesta.json()["nuevos"] == 1
    assert respuesta.headers["Cache-Control"] == "no-store"
    sincronizar.assert_awaited_once_with()


@pytest.mark.parametrize("metodo", ["GET", "POST"])
@pytest.mark.parametrize("token", [None, "incorrecto", "mi_token_secreto_local"])
def test_sin_autorizacion_no_ejecuta(http, monkeypatch, metodo, token):
    sincronizar = AsyncMock()
    monkeypatch.setattr(admin, "sincronizar_drive_completo", sincronizar)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    assert http.request(metodo, "/admin/sync", headers=headers).status_code == 401
    sincronizar.assert_not_called()


@pytest.mark.parametrize("estado,codigo", [("parcial", 207), ("error", 503)])
def test_estado_http(http, monkeypatch, estado, codigo):
    monkeypatch.setattr(admin, "sincronizar_drive_completo", AsyncMock(
        return_value=ResumenSincronizacionDrive(estado=estado, errores=1)))
    respuesta = http.post("/admin/sync", headers={"Authorization": f"Bearer {TOKEN}"})
    assert respuesta.status_code == codigo


def test_otra_sincronizacion(http, monkeypatch):
    monkeypatch.setattr(admin, "sincronizar_drive_completo", AsyncMock(
        side_effect=SincronizacionEnCurso()))
    assert http.post("/admin/sync", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 409
