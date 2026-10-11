"""Fixtures compartidas de las pruebas."""

import pytest
from fastapi.testclient import TestClient

from app.config import obtener_configuracion
from app.main import app


@pytest.fixture(autouse=True)
def configuracion_aislada(monkeypatch, tmp_path):
    """Aísla cada prueba de las variables de entorno y del .env del desarrollador."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ENTORNO", raising=False)
    monkeypatch.delenv("VERSION_APP", raising=False)
    obtener_configuracion.cache_clear()
    yield
    obtener_configuracion.cache_clear()


@pytest.fixture
def cliente() -> TestClient:
    """Cliente HTTP de prueba contra la aplicación."""
    return TestClient(app)
