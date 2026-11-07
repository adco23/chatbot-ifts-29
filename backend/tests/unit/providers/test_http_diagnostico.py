"""DEBUG_LLM sólo expone metadatos, incluso si el proveedor recibe texto sensible."""

import asyncio
import logging

import httpx
import pytest

from app.providers.base import ErrorProveedor, SolicitudLLM
from app.providers.gemini import ProveedorGemini
from app.providers.groq import ProveedorGroq


@pytest.mark.parametrize("clase", [ProveedorGemini, ProveedorGroq])
@pytest.mark.parametrize("caso", ["ok", "http_503", "timeout", "json_invalido"])
def test_debug_solo_metadatos_sin_textos_encabezados_ni_secretos(
    monkeypatch, caplog, capsys, clase, caso,
):
    monkeypatch.setenv("DEBUG_LLM", "1")
    monkeypatch.setenv("ENTORNO", "local")
    caplog.set_level(logging.DEBUG, logger="app.providers._http")
    clave = "clave-privada-de-prueba"
    consulta = "Mi DNI es 12345678 y mi correo es persona@ejemplo.edu.ar"
    contexto = "fragmento-institucional-confidencial"
    prompt = "prompt-privado-del-sistema"
    respuesta = "respuesta-privada-del-proveedor"

    def manejar(request):
        if caso == "timeout":
            raise httpx.ReadTimeout(consulta, request=request)
        if caso == "http_503":
            return httpx.Response(503, json={"error": {"message": consulta + contexto}})
        if caso == "json_invalido":
            return httpx.Response(200, text=respuesta)
        if clase is ProveedorGemini:
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": respuesta}]}}],
            })
        return httpx.Response(200, json={"choices": [{"message": {"content": respuesta}}]})

    async def ejecutar():
        async with httpx.AsyncClient(transport=httpx.MockTransport(manejar)) as cliente:
            proveedor = clase(clave, "modelo-prueba", 2, cliente=cliente)
            solicitud = SolicitudLLM(
                instrucciones_sistema=prompt, contexto=contexto, consulta=consulta,
            )
            if caso == "ok":
                assert (await proveedor.generar(solicitud)).texto == respuesta
            else:
                with pytest.raises(ErrorProveedor):
                    await proveedor.generar(solicitud)

    asyncio.run(ejecutar())
    registros = [r for r in caplog.records if r.name == "app.providers._http"]
    assert len(registros) == 2
    assert "etapa=request" in registros[0].getMessage()
    ultimo = registros[-1].getMessage()
    assert "duracion_segundos=" in ultimo
    if caso == "timeout":
        assert "etapa=error tipo=ErrorTemporal" in ultimo
    else:
        assert "etapa=response" in ultimo and "bytes=" in ultimo
        assert f"http={503 if caso == 'http_503' else 200}" in ultimo
    salida = capsys.readouterr()
    assert salida.out == salida.err == ""
    for secreto in (clave, consulta, contexto, prompt, respuesta, "12345678", "persona@ejemplo.edu.ar"):
        assert secreto not in caplog.text
    assert "authorization" not in " ".join(r.getMessage() for r in registros).lower()
    assert "x-goog-api-key" not in " ".join(r.getMessage() for r in registros).lower()


@pytest.mark.parametrize("debug,entorno", [("0", "local"), ("1", "produccion")])
def test_diagnostico_desactivado_o_produccion_no_emiten_debug(
    monkeypatch, caplog, capsys, debug, entorno,
):
    monkeypatch.setenv("DEBUG_LLM", debug)
    monkeypatch.setenv("ENTORNO", entorno)
    caplog.set_level(logging.DEBUG, logger="app.providers._http")

    async def ejecutar():
        transporte = httpx.MockTransport(lambda request: httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": "OK"}]}}],
        }))
        async with httpx.AsyncClient(transport=transporte) as cliente:
            proveedor = ProveedorGemini("clave-ficticia", "modelo-prueba", 2, cliente=cliente)
            await proveedor.generar(SolicitudLLM(
                instrucciones_sistema="", contexto="", consulta="Consulta institucional",
            ))

    asyncio.run(ejecutar())
    assert not any(r.name == "app.providers._http" for r in caplog.records)
    salida = capsys.readouterr()
    assert salida.out == salida.err == ""
