"""Respaldo LLM con adaptadores reales y HTTP simulado: nunca usa la red."""

import asyncio
import logging
import ssl
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.providers.base import ContenidoBloqueado, ErrorTemporal, TodosLosProveedoresFallaron
from app.providers.gemini import ProveedorGemini
from app.providers.gestor import GestorProveedores
from app.providers.groq import ProveedorGroq
from tests.unit.providers.ayudas import CLAVE, CONSULTA, CONTEXTO, INSTRUCCIONES, crear_solicitud

PRIVADO = "detalle-remoto-que-no-se-debe-registrar"
RESPUESTA = "Respuesta privada del modelo."


def gemini_ok(texto=RESPUESTA):
    return httpx.Response(200, json={"candidates": [{
        "content": {"parts": [{"text": texto}]}, "finishReason": "STOP",
    }]})


def groq_ok(texto=RESPUESTA):
    return httpx.Response(200, json={"choices": [{"message": {"content": texto}}]})


async def escenario(gemini, groq, *, timeout=0.2, presupuesto=1, backoff=0, reintentos=1):
    llamadas = []

    async def manejar(request):
        nombre = "gemini" if request.url.host == "generativelanguage.googleapis.com" else "groq"
        llamadas.append(nombre)
        cola = gemini if nombre == "gemini" else groq
        valor = cola.pop(0) if len(cola) > 1 else cola[0]
        if isinstance(valor, Exception):
            raise valor
        if callable(valor):
            return await valor(request)
        return valor

    async with httpx.AsyncClient(transport=httpx.MockTransport(manejar)) as cliente:
        principal = ProveedorGemini(CLAVE, "gemini-prueba", timeout, cliente=cliente)
        respaldo = ProveedorGroq(CLAVE, "groq-prueba", timeout, cliente=cliente)
        gestor = GestorProveedores([principal, respaldo], presupuesto,
                                  backoff_segundos=backoff, reintentos_principal=reintentos)
        try:
            return await gestor.generar_con_respaldo(crear_solicitud()), llamadas
        except TodosLosProveedoresFallaron as error:
            return error, llamadas


def test_gemini_ok_no_duplica_ni_llama_groq():
    resultado, llamadas = asyncio.run(escenario([gemini_ok()], [groq_ok()]))
    assert resultado.proveedor == "gemini" and llamadas == ["gemini"]


def test_temporal_retry_exitoso_no_llama_groq(caplog):
    caplog.set_level(logging.INFO)
    resultado, llamadas = asyncio.run(escenario([httpx.Response(503), gemini_ok()], [groq_ok()]))
    assert resultado.proveedor == "gemini" and llamadas == ["gemini", "gemini"]
    assert "causa=http_5xx http=503" in caplog.text
    assert "Se reintenta el proveedor gemini" in caplog.text


@pytest.mark.parametrize("falla,causa", [
    (httpx.Response(503), "http_5xx"),
    (httpx.ReadTimeout(PRIVADO), "timeout"),
    (httpx.ConnectError(PRIVADO), "conexion"),
    (httpx.Response(200, text=PRIVADO), "respuesta_invalida"),
    (httpx.Response(200, json={"candidates": [{"finishReason": "MAX_TOKENS"}]}), "respuesta_invalida"),
    (httpx.Response(200, json={"candidates": [{"finishReason": PRIVADO}]}), "respuesta_invalida"),
    (httpx.Response(200, json={"candidates": [{"finishReason": {"dato": PRIVADO}}]}), "respuesta_invalida"),
])
def test_gemini_temporal_agota_retry_y_groq_responde(falla, causa, caplog):
    caplog.set_level(logging.INFO)
    resultado, llamadas = asyncio.run(escenario([falla], [groq_ok()]))
    assert resultado.proveedor == "groq" and llamadas == ["gemini", "gemini", "groq"]
    assert f"causa={causa}" in caplog.text
    assert "fallback a Groq iniciado" in caplog.text
    for privado in (CLAVE, CONSULTA, CONTEXTO, INSTRUCCIONES, PRIVADO, RESPUESTA):
        assert privado not in caplog.text
    assert all(r.exc_info is None for r in caplog.records)


def test_429_pasa_directamente_a_groq(caplog):
    resultado, llamadas = asyncio.run(escenario([httpx.Response(429, json={
        "error": {"message": PRIVADO, "status": "RESOURCE_EXHAUSTED"},
    })], [groq_ok()]))
    assert resultado.proveedor == "groq" and llamadas == ["gemini", "groq"]
    assert "causa=http_429 http=429" in caplog.text and PRIVADO not in caplog.text


@pytest.mark.parametrize("estado", [400, 401, 403, 404])
def test_permanente_sin_retry_pero_con_respaldo(estado, caplog):
    resultado, llamadas = asyncio.run(escenario([httpx.Response(estado, json={
        "error": {"message": PRIVADO},
    })], [groq_ok()]))
    assert resultado.proveedor == "groq" and llamadas == ["gemini", "groq"]
    assert f"causa=http_4xx http={estado}" in caplog.text and PRIVADO not in caplog.text


def test_ambos_fallan_informa_intentos_sin_reintentar_groq():
    resultado, llamadas = asyncio.run(escenario([httpx.Response(503)], [httpx.Response(503)]))
    assert isinstance(resultado, TodosLosProveedoresFallaron)
    assert llamadas == ["gemini", "gemini", "groq"]
    assert [e.estado_http for e in resultado.errores] == [503, 503, 503]


def test_retry_se_puede_desactivar_sin_perder_respaldo():
    resultado, llamadas = asyncio.run(escenario([httpx.Response(503)], [groq_ok()], reintentos=0))
    assert resultado.proveedor == "groq" and llamadas == ["gemini", "groq"]


def test_groq_sin_texto_informa_limite_de_tokens(caplog):
    resultado, _ = asyncio.run(escenario([httpx.Response(503)], [httpx.Response(200, json={
        "choices": [{"message": {"content": None, "reasoning": PRIVADO}, "finish_reason": "length"}],
    })]))
    assert isinstance(resultado, TodosLosProveedoresFallaron)
    assert resultado.errores[-1].causa == "respuesta_invalida"
    assert resultado.errores[-1].detalle == "length"
    assert "detalle=length" in caplog.text and PRIVADO not in caplog.text


def test_timeout_del_gestor_reserva_respaldo_y_cancela_antes_de_retry(caplog):
    activos = 0

    async def lento(request):
        nonlocal activos
        activos += 1
        assert activos == 1
        try:
            await asyncio.sleep(1)
            return gemini_ok()
        finally:
            activos -= 1

    inicio = time.monotonic()
    resultado, llamadas = asyncio.run(escenario([lento], [groq_ok()], timeout=0.2, presupuesto=0.15))
    assert resultado.proveedor == "groq" and llamadas == ["gemini", "gemini", "groq"]
    assert time.monotonic() - inicio < 0.5 and activos == 0
    assert "causa=timeout" in caplog.text and "detalle=presupuesto_intento" in caplog.text


def test_certificado_informa_causa_segura_sin_desactivar_tls(caplog):
    fallo = httpx.ConnectError(PRIVADO)
    fallo.__cause__ = ssl.SSLCertVerificationError(PRIVADO)
    resultado, _ = asyncio.run(escenario([fallo], [groq_ok()]))
    assert resultado.proveedor == "groq"
    assert "causa=conexion" in caplog.text and "detalle=tls" in caplog.text
    assert PRIVADO not in caplog.text


def test_bloqueo_no_se_reintenta_ni_se_elude_con_groq():
    async def ejecutar():
        with pytest.raises(ContenidoBloqueado):
            await escenario([httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})], [groq_ok()])
    asyncio.run(ejecutar())


def test_excepcion_inesperada_y_respuesta_none_tienen_diagnostico(caplog):
    async def ejecutar():
        principal = SimpleNamespace(nombre="gemini", generar=AsyncMock(side_effect=RuntimeError(PRIVADO)))
        respaldo = SimpleNamespace(nombre="groq", generar=AsyncMock(return_value=None))
        with pytest.raises(TodosLosProveedoresFallaron):
            await GestorProveedores([principal, respaldo], 1, backoff_segundos=0).generar_con_respaldo(crear_solicitud())
        assert principal.generar.await_count == 2 and respaldo.generar.await_count == 1
    asyncio.run(ejecutar())
    assert "causa=otra" in caplog.text and "causa=respuesta_invalida" in caplog.text
    assert PRIVADO not in caplog.text


def test_cancelacion_externa_no_inicia_otro_intento():
    async def ejecutar():
        iniciado = asyncio.Event()
        async def generar(solicitud):
            iniciado.set()
            await asyncio.Event().wait()
        principal = SimpleNamespace(nombre="gemini", generar=AsyncMock(side_effect=generar))
        respaldo = SimpleNamespace(nombre="groq", generar=AsyncMock())
        tarea = asyncio.create_task(GestorProveedores([principal, respaldo], 5).generar_con_respaldo(crear_solicitud()))
        await iniciado.wait()
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea
        assert principal.generar.await_count == 1
        respaldo.generar.assert_not_called()
    asyncio.run(ejecutar())
