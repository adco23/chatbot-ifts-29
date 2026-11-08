"""Fixtures compartidas de las pruebas."""

import pytest
from fastapi.testclient import TestClient

from app.config import Configuracion, obtener_configuracion
from app.main import app
from app.providers.dependencias import obtener_gestor_proveedores


@pytest.fixture(autouse=True)
def configuracion_aislada(monkeypatch, tmp_path):
    """Aísla cada prueba de las variables de entorno y del .env del desarrollador."""
    monkeypatch.chdir(tmp_path)
    for campo in Configuracion.model_fields:
        monkeypatch.delenv(campo.upper(), raising=False)
    obtener_configuracion.cache_clear()
    obtener_gestor_proveedores.cache_clear()
    yield
    obtener_configuracion.cache_clear()
    obtener_gestor_proveedores.cache_clear()


@pytest.fixture
def cliente() -> TestClient:
    """Cliente HTTP de prueba contra la aplicación."""
    return TestClient(app)
