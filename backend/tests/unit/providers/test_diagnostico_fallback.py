"""Decisiones visibles con WARNING y presupuesto virtual de 9 s, sin red."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.providers import gestor as modulo
from app.providers.base import ErrorTemporal, RespuestaLLM, TodosLosProveedoresFallaron
from app.providers.dependencias import crear_gestor_proveedores
from app.providers.gestor import GestorProveedores
from app.settings import Configuracion
from tests.unit.providers.ayudas import CLAVE, crear_solicitud


@pytest.mark.parametrize("consumos", [
    (0.61, 0.64), ("limite", 0.75), ("limite", "limite"),
])
def test_nueve_segundos_reservan_intento_groq_despues_de_gemini(
    monkeypatch, caplog, consumos,
):
    caplog.set_level(logging.WARNING)
    reloj = [100.0]
    llamadas = []

    async def esperar(segundos):
        reloj[0] += segundos

    monkeypatch.setattr(modulo, "time", SimpleNamespace(monotonic=lambda: reloj[0]))
    monkeypatch.setattr(modulo, "asyncio", SimpleNamespace(sleep=esperar))
    gestor = GestorProveedores([
        SimpleNamespace(nombre="gemini", timeout_segundos=6),
        SimpleNamespace(nombre="groq", timeout_segundos=3),
    ], 9, backoff_segundos=0.25)

    async def intentar(proveedor, solicitud, limite):
        llamadas.append((proveedor.nombre, limite))
        if proveedor.nombre == "gemini":
            consumo = consumos[len(llamadas) - 1]
            reloj[0] += limite if consumo == "limite" else consumo
            raise ErrorTemporal(proveedor="gemini", causa="http_5xx", estado_http=503)
        reloj[0] += 2.99
        return RespuestaLLM("ok", "groq", "prueba")

    gestor._intentar = AsyncMock(side_effect=intentar)
    resultado = asyncio.run(gestor.generar_con_respaldo(crear_solicitud()))
    assert resultado.proveedor == "groq"
    assert [p for p, _ in llamadas] == ["gemini", "gemini", "groq"]
    assert llamadas[0][1] == pytest.approx(2.875)
    assert llamadas[2][1] == pytest.approx(3)
    assert reloj[0] - 100 <= 9
    assert "fallback a Groq iniciado" in caplog.text and "Groq respondió" in caplog.text
    assert "Groq omitido" not in caplog.text


def test_groq_omitido_por_presupuesto_se_distingue_de_no_configurado(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    reloj = [100.0]
    monkeypatch.setattr(modulo, "time", SimpleNamespace(monotonic=lambda: reloj[0]))
    gestor = GestorProveedores([
        SimpleNamespace(nombre="gemini", timeout_segundos=6),
        SimpleNamespace(nombre="groq", timeout_segundos=3),
    ], 9)

    async def evento_bloqueado(proveedor, solicitud, limite):
        # Simula una pausa externa del proceso que agota el deadline global.
        reloj[0] += 9
        raise ErrorTemporal(proveedor=proveedor.nombre, causa="timeout")

    gestor._intentar = AsyncMock(side_effect=evento_bloqueado)
    with pytest.raises(TodosLosProveedoresFallaron):
        asyncio.run(gestor.generar_con_respaldo(crear_solicitud()))
    assert "Groq omitido: presupuesto insuficiente" in caplog.text
    assert "Groq omitido: no configurado" not in caplog.text
    assert "fallback a Groq iniciado" not in caplog.text
    gestor._intentar.assert_awaited_once()


@pytest.mark.parametrize("clave_groq,orden,motivo", [
    (None, ["gemini", "groq"], "no configurado"),
    (CLAVE, ["gemini"], "excluido del orden configurado"),
])
def test_configuracion_real_construida_explica_omision_segura(caplog, clave_groq, orden, motivo):
    caplog.set_level(logging.WARNING)
    config = Configuracion(gemini_api_key=CLAVE, groq_api_key=clave_groq,
                           llm_orden_proveedores=orden, llm_backoff_segundos=0)
    gestor = crear_gestor_proveedores(config)
    gestor.proveedores[0].generar = AsyncMock(side_effect=ErrorTemporal(CLAVE))

    async def ejecutar():
        try:
            with pytest.raises(TodosLosProveedoresFallaron):
                await gestor.generar_con_respaldo(crear_solicitud())
        finally:
            for proveedor in gestor.proveedores:
                await proveedor.aclose()

    asyncio.run(ejecutar())
    assert f"Groq omitido: {motivo}" in caplog.text
    assert "groq_en_gestor=False" in caplog.text
    assert CLAVE not in caplog.text


def test_fallo_groq_se_registra_explicito_a_nivel_warning(caplog):
    caplog.set_level(logging.WARNING)
    principal = SimpleNamespace(nombre="gemini", generar=AsyncMock(side_effect=ErrorTemporal(CLAVE)))
    respaldo = SimpleNamespace(nombre="groq", generar=AsyncMock(side_effect=ErrorTemporal(
        CLAVE, causa="http_5xx", estado_http=503,
    )))
    with pytest.raises(TodosLosProveedoresFallaron):
        asyncio.run(GestorProveedores([principal, respaldo], 9, backoff_segundos=0)
                    .generar_con_respaldo(crear_solicitud()))
    assert "fallback a Groq iniciado" in caplog.text
    assert "Groq falló: proveedor=groq categoria=ErrorTemporal causa=http_5xx http=503" in caplog.text
    assert CLAVE not in caplog.text and "Groq omitido" not in caplog.text
