"""Pruebas de integración con los proveedores reales.

Hacen llamadas de red y consumen un poco de la cuota gratuita (tres llamadas en total).
Están excluidas por defecto: ni `pytest` ni la integración continua las ejecutan.

Para correrlas, desde backend/ y con las claves cargadas en .env o en el entorno:

    pytest -m integracion

Cada prueba se omite sola si falta la clave del proveedor que necesita.
"""

import asyncio

import pytest

from app.settings import Configuracion
from app.providers import CuotaAgotada, GestorProveedores, RespuestaLLM, SolicitudLLM
from app.providers._http import ProveedorHTTP
from app.providers.dependencias import crear_proveedor
from app.providers.falso import ProveedorFalso

pytestmark = pytest.mark.integracion

SOLICITUD = SolicitudLLM(
    instrucciones_sistema="Respondé en español, en una sola oración breve.",
    contexto="",
    consulta="Saludá con la palabra hola.",
)


def proveedor_real(nombre: str, config: Configuracion) -> ProveedorHTTP:
    if nombre not in config.proveedores_disponibles:
        pytest.skip(f"Falta la clave de {nombre} ({nombre.upper()}_API_KEY).")
    return crear_proveedor(nombre, config)


def comprobar_respuesta(respuesta: RespuestaLLM, nombre: str) -> None:
    assert respuesta.texto.strip()
    assert respuesta.proveedor == nombre
    assert respuesta.modelo


@pytest.mark.parametrize("nombre", ["gemini", "groq"])
def test_proveedor_real_responde(nombre, configuracion_real):
    proveedor = proveedor_real(nombre, configuracion_real)

    async def escenario() -> RespuestaLLM:
        try:
            return await proveedor.generar(SOLICITUD)
        finally:
            await proveedor.aclose()

    comprobar_respuesta(asyncio.run(escenario()), nombre)


def test_el_gestor_usa_el_respaldo_real_cuando_el_primero_falla(configuracion_real):
    if not configuracion_real.proveedores_disponibles:
        pytest.skip("No hay ninguna clave configurada.")
    # Se usa como respaldo el último disponible, que es el respaldo configurado.
    nombre = configuracion_real.proveedores_disponibles[-1]
    respaldo = proveedor_real(nombre, configuracion_real)
    primero = ProveedorFalso([CuotaAgotada()], nombre="principal-simulado")
    gestor = GestorProveedores(
        [primero, respaldo], presupuesto_segundos=configuracion_real.llm_presupuesto_segundos
    )

    async def escenario() -> RespuestaLLM:
        try:
            return await gestor.generar_con_respaldo(SOLICITUD)
        finally:
            await respaldo.aclose()

    respuesta = asyncio.run(escenario())

    assert primero.llamadas == 1
    comprobar_respuesta(respuesta, nombre)
