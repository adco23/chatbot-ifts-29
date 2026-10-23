"""Fixtures compartidas de las pruebas."""

import pytest
from fastapi.testclient import TestClient

from app.settings import Configuracion, obtener_configuracion
from app.db.conexion import obtener_cliente_supabase
from app.main import app
from app.providers.dependencias import obtener_gestor_proveedores
from app.providers.embeddings.dependencias import obtener_gestor_embeddings
from app.services.limite_solicitudes import obtener_contador_solicitudes

# Funciones que guardan su resultado por proceso: se vacían antes y después de cada
# prueba para que ninguna herede la configuración ni el estado de otra.
_CON_CACHE = (
    obtener_configuracion,
    obtener_gestor_proveedores,
    obtener_gestor_embeddings,
    obtener_contador_solicitudes,
    obtener_cliente_supabase,
)


def _vaciar_caches() -> None:
    for funcion in _CON_CACHE:
        funcion.cache_clear()


@pytest.fixture(autouse=True)
def configuracion_aislada(monkeypatch, tmp_path):
    """Aísla cada prueba de las variables de entorno y del .env del desarrollador."""
    monkeypatch.chdir(tmp_path)
    for campo in Configuracion.model_fields:
        monkeypatch.delenv(campo.upper(), raising=False)
    _vaciar_caches()
    yield
    _vaciar_caches()


@pytest.fixture
def cliente() -> TestClient:
    """Cliente HTTP de prueba contra la aplicación."""
    return TestClient(app)
