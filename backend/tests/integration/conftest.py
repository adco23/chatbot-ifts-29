"""Fixtures de las pruebas de integración."""

from pathlib import Path

import pytest

from app.settings import Configuracion

RAIZ_BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def configuracion_aislada():
    """Reemplaza la fixture general: acá sí se usan el entorno y el .env reales."""
    yield


@pytest.fixture
def configuracion_real() -> Configuracion:
    """Configuración leída de las variables de entorno y de backend/.env."""
    return Configuracion(_env_file=RAIZ_BACKEND / ".env")
