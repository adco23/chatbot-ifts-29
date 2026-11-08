"""Pruebas de `GestorProveedores`, con `ProveedorFalso` en lugar de proveedores reales."""

import asyncio
import logging
import time

import pytest

from app.providers import (
    ContenidoBloqueado,
    CuotaAgotada,
    ErrorNoRecuperable,
    ErrorTemporal,
    GestorProveedores,
    ProveedorLLM,
    RespuestaLLM,
    SinProveedoresConfigurados,
    SolicitudLLM,
    TodosLosProveedoresFallaron,
)
from app.providers.falso import ProveedorFalso
from tests.unit.providers.ayudas import CONSULTA, CONTEXTO, INSTRUCCIONES, crear_solicitud

TEXTO_RESPUESTA = "Las clases empiezan en marzo."


def generar(gestor: GestorProveedores) -> RespuestaLLM:
    return asyncio.run(gestor.generar_con_respaldo(crear_solicitud()))


def test_responde_el_primero_y_no_se_llama_al_segundo():
    primero = ProveedorFalso([TEXTO_RESPUESTA], nombre="primero")
    segundo = ProveedorFalso(["otra"], nombre="segundo")

    respuesta = generar(GestorProveedores([primero, segundo], presupuesto_segundos=5))

    assert respuesta.texto == TEXTO_RESPUESTA
    assert respuesta.proveedor == "primero"
    assert (primero.llamadas, segundo.llamadas) == (1, 0)


@pytest.mark.parametrize("excepcion", [CuotaAgotada, ErrorTemporal])
def test_falla_recuperable_en_el_primero_y_responde_el_segundo(excepcion):
    primero = ProveedorFalso([excepcion()], nombre="primero")
    segundo = ProveedorFalso([TEXTO_RESPUESTA], nombre="segundo")

    respuesta = generar(GestorProveedores([primero, segundo], presupuesto_segundos=5))

    assert respuesta.proveedor == "segundo"
    assert (primero.llamadas, segundo.llamadas) == (2 if excepcion is ErrorTemporal else 1, 1)


def test_bloqueo_se_propaga_y_no_se_llama_al_segundo():
    error = ContenidoBloqueado()
    primero = ProveedorFalso([error], nombre="primero")
    segundo = ProveedorFalso([TEXTO_RESPUESTA], nombre="segundo")

    with pytest.raises(ContenidoBloqueado) as info:
        generar(GestorProveedores([primero, segundo], presupuesto_segundos=5))

    assert info.value is error
    assert info.value.proveedor == "primero"
    assert segundo.llamadas == 0


def test_error_permanente_no_reintenta_principal_pero_prueba_respaldo():
    primero = ProveedorFalso([ErrorNoRecuperable()], nombre="primero")
    segundo = ProveedorFalso([TEXTO_RESPUESTA], nombre="segundo")
    assert generar(GestorProveedores([primero, segundo], 5)).proveedor == "segundo"
    assert (primero.llamadas, segundo.llamadas) == (1, 1)


def test_fallan_todos_y_se_informa_la_lista_completa_de_errores():
    cuota = CuotaAgotada()
    temporal = ErrorTemporal()
    gestor = GestorProveedores(
        [ProveedorFalso([cuota], nombre="primero"), ProveedorFalso([temporal], nombre="segundo")],
        presupuesto_segundos=5,
    )

    with pytest.raises(TodosLosProveedoresFallaron) as info:
        generar(gestor)

    assert info.value.errores == [cuota, temporal]
    assert "primero: CuotaAgotada" in str(info.value)
    assert "segundo: ErrorTemporal" in str(info.value)


def test_lista_vacia_informa_que_no_hay_proveedores():
    with pytest.raises(TodosLosProveedoresFallaron) as info:
        generar(GestorProveedores([], presupuesto_segundos=5))

    assert isinstance(info.value, SinProveedoresConfigurados)
    assert info.value.errores == []
    assert str(info.value) == "No hay proveedores de modelo de lenguaje configurados."


def test_proveedor_lento_es_cortado_reservando_tiempo_al_respaldo():
    lento = ProveedorFalso([TEXTO_RESPUESTA], nombre="lento", latencia_segundos=2)
    segundo = ProveedorFalso([TEXTO_RESPUESTA], nombre="segundo")

    inicio = time.monotonic()
    respuesta = generar(GestorProveedores([lento, segundo], presupuesto_segundos=0.1))
    duracion = time.monotonic() - inicio

    assert duracion < 1
    assert respuesta.proveedor == "segundo"
    assert (lento.llamadas, segundo.llamadas) == (1, 1)


def test_el_segundo_solo_dispone_del_tiempo_restante():
    primero = ProveedorFalso([ErrorTemporal()], nombre="primero", latencia_segundos=0.15)
    segundo = ProveedorFalso([TEXTO_RESPUESTA], nombre="segundo", latencia_segundos=2)

    inicio = time.monotonic()
    with pytest.raises(TodosLosProveedoresFallaron) as info:
        generar(GestorProveedores([primero, segundo], presupuesto_segundos=0.3))
    duracion = time.monotonic() - inicio

    assert duracion < 1
    assert [e.proveedor for e in info.value.errores] == ["primero", "segundo"]
    assert (primero.llamadas, segundo.llamadas) == (1, 1)


def test_excepcion_fuera_del_contrato_se_trata_como_error_temporal():
    class ProveedorRoto(ProveedorLLM):
        @property
        def nombre(self) -> str:
            return "roto"

        async def generar(self, solicitud: SolicitudLLM) -> RespuestaLLM:
            raise RuntimeError(f"falló con: {solicitud.consulta}")

    segundo = ProveedorFalso([TEXTO_RESPUESTA], nombre="segundo")

    respuesta = generar(GestorProveedores([ProveedorRoto(), segundo], presupuesto_segundos=5))

    assert respuesta.proveedor == "segundo"


def test_no_guarda_estado_entre_llamadas():
    primero = ProveedorFalso([CuotaAgotada(), TEXTO_RESPUESTA], nombre="primero")
    segundo = ProveedorFalso(["respaldo"], nombre="segundo")
    gestor = GestorProveedores([primero, segundo], presupuesto_segundos=5)

    assert generar(gestor).proveedor == "segundo"
    # La falla anterior no saca al primero de la lista.
    assert generar(gestor).proveedor == "primero"


def test_los_registros_informan_proveedor_categoria_y_duracion_sin_contenido(caplog):
    caplog.set_level(logging.DEBUG)
    primero = ProveedorFalso([CuotaAgotada(f"detalle con {CONSULTA}")], nombre="primero")
    segundo = ProveedorFalso([TEXTO_RESPUESTA], nombre="segundo")

    generar(GestorProveedores([primero, segundo], presupuesto_segundos=5))

    registros = "\n".join(registro.getMessage() for registro in caplog.records)
    assert "primero" in registros
    assert "CuotaAgotada" in registros
    assert "segundo" in registros
    assert " s." in registros
    for dato in (CONSULTA, CONTEXTO, INSTRUCCIONES, TEXTO_RESPUESTA):
        assert dato not in registros
    assert all(registro.exc_info is None for registro in caplog.records)


@pytest.mark.parametrize(
    "proveedores",
    [
        [],
        [ProveedorFalso([ErrorTemporal()], nombre="a"), ProveedorFalso([CuotaAgotada()])],
        [ProveedorFalso([ErrorNoRecuperable()], nombre="a")],
    ],
)
def test_los_registros_de_fallas_no_incluyen_contenido(caplog, proveedores):
    caplog.set_level(logging.DEBUG)

    with pytest.raises(Exception):  # noqa: B017 - interesa el registro, no el tipo
        generar(GestorProveedores(proveedores, presupuesto_segundos=5))

    assert caplog.records
    for dato in (CONSULTA, CONTEXTO, INSTRUCCIONES):
        assert dato not in caplog.text
