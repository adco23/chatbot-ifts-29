"""Pruebas de la construcción del gestor a partir de la configuración."""

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.config import PROVEEDORES_LLM_VALIDOS, obtener_configuracion
from app.providers import GestorProveedores, ProveedorGemini, ProveedorGroq
from app.providers.dependencias import (
    REGISTRO_PROVEEDORES,
    crear_proveedor,
    obtener_gestor_proveedores,
)
from app.providers.falso import ProveedorFalso

# Valores ficticios: no son claves reales ni tienen su formato.
CLAVE_GEMINI = "clave-ficticia-gemini"
CLAVE_GROQ = "clave-ficticia-groq"


def test_sin_claves_construye_un_gestor_vacio():
    gestor = obtener_gestor_proveedores()

    assert gestor.proveedores == ()
    assert gestor.presupuesto_segundos == 9.0


def test_con_claves_construye_los_proveedores_en_el_orden_por_defecto(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", CLAVE_GEMINI)
    monkeypatch.setenv("GROQ_API_KEY", CLAVE_GROQ)

    gestor = obtener_gestor_proveedores()

    assert [type(proveedor) for proveedor in gestor.proveedores] == [ProveedorGemini, ProveedorGroq]
    assert [proveedor.nombre for proveedor in gestor.proveedores] == ["gemini", "groq"]


def test_respeta_el_orden_los_modelos_y_los_tiempos_configurados(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", CLAVE_GEMINI)
    monkeypatch.setenv("GROQ_API_KEY", CLAVE_GROQ)
    monkeypatch.setenv("LLM_ORDEN_PROVEEDORES", "groq,gemini")
    monkeypatch.setenv("GEMINI_MODELO", "modelo-gemini")
    monkeypatch.setenv("GROQ_MODELO", "modelo-groq")
    monkeypatch.setenv("GEMINI_TIMEOUT_SEGUNDOS", "4")
    monkeypatch.setenv("GROQ_TIMEOUT_SEGUNDOS", "2")
    monkeypatch.setenv("LLM_PRESUPUESTO_SEGUNDOS", "7")

    gestor = obtener_gestor_proveedores()
    groq, gemini = gestor.proveedores

    assert (groq.nombre, groq.modelo, groq._timeout_segundos) == ("groq", "modelo-groq", 2.0)
    assert (gemini.nombre, gemini.modelo, gemini._timeout_segundos) == (
        "gemini",
        "modelo-gemini",
        4.0,
    )
    assert groq._api_key == CLAVE_GROQ
    assert gemini._api_key == CLAVE_GEMINI
    assert gestor.presupuesto_segundos == 7.0


def test_solo_incluye_los_proveedores_con_clave(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", CLAVE_GROQ)

    gestor = obtener_gestor_proveedores()

    assert [proveedor.nombre for proveedor in gestor.proveedores] == ["groq"]


def test_el_gestor_se_construye_una_sola_vez():
    assert obtener_gestor_proveedores() is obtener_gestor_proveedores()


def test_el_registro_coincide_con_los_nombres_validos_de_la_configuracion():
    assert set(REGISTRO_PROVEEDORES) == set(PROVEEDORES_LLM_VALIDOS)
    for nombre, clase in REGISTRO_PROVEEDORES.items():
        assert clase(api_key="x", modelo="m", timeout_segundos=1).nombre == nombre


@pytest.mark.parametrize("nombre", ["gemini", "falso", "openai"])
def test_crear_proveedor_sin_clave_o_sin_registrar_lanza_error(nombre):
    with pytest.raises(ValueError):
        crear_proveedor(nombre, obtener_configuracion())


def test_se_puede_sobrescribir_con_dependency_overrides():
    app = FastAPI()

    @app.get("/proveedores")
    def listar(
        gestor: Annotated[GestorProveedores, Depends(obtener_gestor_proveedores)],
    ) -> list[str]:
        return [proveedor.nombre for proveedor in gestor.proveedores]

    cliente = TestClient(app)
    assert cliente.get("/proveedores").json() == []

    gestor_de_prueba = GestorProveedores([ProveedorFalso(["hola"])], presupuesto_segundos=1)
    app.dependency_overrides[obtener_gestor_proveedores] = lambda: gestor_de_prueba

    assert cliente.get("/proveedores").json() == ["falso"]
