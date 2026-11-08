"""Pruebas de `ProveedorFalso`."""

import asyncio
import time

import pytest

from app.config import PROVEEDORES_LLM_VALIDOS
from app.providers import CuotaAgotada, ErrorTemporal, RespuestaLLM
from app.providers.dependencias import REGISTRO_PROVEEDORES
from app.providers.falso import MODELO_FALSO, ProveedorFalso
from tests.unit.providers.ayudas import crear_solicitud


def test_devuelve_respuestas_y_lanza_errores_en_orden():
    proveedor = ProveedorFalso(["primera", CuotaAgotada(), "tercera"])

    async def escenario() -> None:
        primera = await proveedor.generar(crear_solicitud())
        assert primera == RespuestaLLM(texto="primera", proveedor="falso", modelo=MODELO_FALSO)
        with pytest.raises(CuotaAgotada) as info:
            await proveedor.generar(crear_solicitud())
        assert info.value.proveedor == "falso"
        assert (await proveedor.generar(crear_solicitud())).texto == "tercera"

    asyncio.run(escenario())


def test_al_agotarse_la_secuencia_repite_el_ultimo_resultado():
    proveedor = ProveedorFalso(["uno", ErrorTemporal()])

    async def escenario() -> None:
        await proveedor.generar(crear_solicitud())
        for _ in range(3):
            with pytest.raises(ErrorTemporal):
                await proveedor.generar(crear_solicitud())

    asyncio.run(escenario())
    assert proveedor.llamadas == 4


def test_cuenta_las_llamadas_y_guarda_la_ultima_solicitud():
    proveedor = ProveedorFalso(["hola"])
    assert proveedor.llamadas == 0
    assert proveedor.ultima_solicitud is None

    solicitud = crear_solicitud(consulta="segunda consulta")
    asyncio.run(proveedor.generar(crear_solicitud()))
    asyncio.run(proveedor.generar(solicitud))

    assert proveedor.llamadas == 2
    assert proveedor.ultima_solicitud is solicitud


def test_nombre_configurable():
    proveedor = ProveedorFalso(["hola"], nombre="gemini")

    assert proveedor.nombre == "gemini"
    assert asyncio.run(proveedor.generar(crear_solicitud())).proveedor == "gemini"


def test_latencia_simulada():
    proveedor = ProveedorFalso(["hola"], latencia_segundos=0.05)

    inicio = time.monotonic()
    asyncio.run(proveedor.generar(crear_solicitud()))

    # Margen por la resolución del reloj del sistema.
    assert time.monotonic() - inicio >= 0.04


@pytest.mark.parametrize("resultados", [[], (), "texto suelto"])
def test_secuencia_invalida_lanza_error(resultados):
    with pytest.raises(ValueError):
        ProveedorFalso(resultados)


def test_no_se_puede_activar_por_configuracion():
    assert "falso" not in PROVEEDORES_LLM_VALIDOS
    assert "falso" not in REGISTRO_PROVEEDORES
    assert ProveedorFalso not in REGISTRO_PROVEEDORES.values()
