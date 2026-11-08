"""Pruebas de `ProveedorGemini`, sin red: las respuestas las da `httpx.MockTransport`."""

import asyncio
import ssl
from typing import Any

import httpx
import pytest

from app.providers import (
    ContenidoBloqueado,
    CuotaAgotada,
    ErrorNoRecuperable,
    ErrorProveedor,
    ErrorTemporal,
    ProveedorGemini,
    RespuestaLLM,
    SolicitudLLM,
)
from app.providers.formato import armar_mensaje_usuario
from tests.unit.providers.ayudas import (
    CLAVE,
    CONSULTA,
    INSTRUCCIONES,
    Captura,
    afirmar_sin_datos_sensibles,
    cliente_simulado,
    crear_solicitud,
)

MODELO = "gemini-de-prueba"


def respuesta_ok(texto: str = "Empiezan el 16 de marzo.", **cambios: Any) -> httpx.Response:
    cuerpo: dict[str, Any] = {
        "candidates": [
            {"content": {"role": "model", "parts": [{"text": texto}]}, "finishReason": "STOP"}
        ],
        "modelVersion": "gemini-de-prueba-001",
    }
    return httpx.Response(200, json=cuerpo | cambios)


def respuesta_de_error(estado: int, **error: Any) -> httpx.Response:
    # El mensaje repite la consulta, como podría hacerlo el proveedor real.
    return httpx.Response(estado, json={"error": {"message": f"Detalle: {CONSULTA}", **error}})


def generar(
    respuesta: httpx.Response | Exception, solicitud: SolicitudLLM | None = None
) -> tuple[RespuestaLLM, Captura]:
    captura = Captura(respuesta)
    proveedor = ProveedorGemini(
        api_key=CLAVE, modelo=MODELO, timeout_segundos=5, cliente=cliente_simulado(captura)
    )
    return asyncio.run(proveedor.generar(solicitud or crear_solicitud())), captura


def error_al_generar(respuesta: httpx.Response | Exception) -> ErrorProveedor:
    with pytest.raises(ErrorProveedor) as info:
        generar(respuesta)
    assert info.value.proveedor == "gemini"
    afirmar_sin_datos_sensibles(info.value)
    return info.value


def test_respuesta_exitosa():
    respuesta, _ = generar(respuesta_ok())

    assert respuesta == RespuestaLLM(
        texto="Empiezan el 16 de marzo.", proveedor="gemini", modelo="gemini-de-prueba-001"
    )


def test_sin_version_en_la_respuesta_informa_el_modelo_configurado():
    cuerpo = {"candidates": [{"content": {"parts": [{"text": "hola"}]}}]}

    respuesta, _ = generar(httpx.Response(200, json=cuerpo))

    assert respuesta.modelo == MODELO


def test_une_las_partes_y_descarta_las_de_razonamiento():
    partes = [{"text": "razonando...", "thought": True}, {"text": "Hola, "}, {"text": "mundo."}]
    cuerpo = {"candidates": [{"content": {"parts": partes}, "finishReason": "STOP"}]}

    respuesta, _ = generar(httpx.Response(200, json=cuerpo))

    assert respuesta.texto == "Hola, mundo."


def test_la_solicitud_separa_instrucciones_y_mensaje_de_usuario():
    solicitud = crear_solicitud()

    _, captura = generar(respuesta_ok(), solicitud)

    assert captura.ultima.method == "POST"
    assert captura.cuerpo == {
        "systemInstruction": {"parts": [{"text": INSTRUCCIONES}]},
        "contents": [{"role": "user", "parts": [{"text": armar_mensaje_usuario(solicitud)}]}],
    }


def test_sin_instrucciones_no_envia_el_campo():
    _, captura = generar(respuesta_ok(), crear_solicitud(instrucciones_sistema=""))

    assert "systemInstruction" not in captura.cuerpo


@pytest.mark.parametrize(
    ("opciones", "esperado"),
    [
        ({"temperatura": 0.2, "max_tokens": 512}, {"temperature": 0.2, "maxOutputTokens": 512}),
        ({"temperatura": 0.0}, {"temperature": 0.0}),
        ({"max_tokens": 64}, {"maxOutputTokens": 64}),
    ],
)
def test_aplica_temperatura_y_maximo_de_tokens_solo_si_se_indican(opciones, esperado):
    _, captura = generar(respuesta_ok(), crear_solicitud(**opciones))

    assert captura.cuerpo["generationConfig"] == esperado


def test_la_clave_viaja_por_encabezado_y_no_en_la_url():
    _, captura = generar(respuesta_ok())

    assert captura.ultima.headers["x-goog-api-key"] == CLAVE
    assert CLAVE not in str(captura.ultima.url)
    assert captura.ultima.url.query == b""
    assert str(captura.ultima.url).endswith(f"/models/{MODELO}:generateContent")


@pytest.mark.parametrize(
    ("respuesta", "excepcion"),
    [
        (respuesta_de_error(429, status="RESOURCE_EXHAUSTED"), CuotaAgotada),
        (respuesta_de_error(429, code="quota_exceeded"), CuotaAgotada),
        # Límite de uso informado en el cuerpo con un estado que no es 429.
        (respuesta_de_error(403, status="RESOURCE_EXHAUSTED"), CuotaAgotada),
        (respuesta_de_error(500, status="INTERNAL"), ErrorTemporal),
        (respuesta_de_error(503, status="UNAVAILABLE"), ErrorTemporal),
        (respuesta_de_error(504, status="DEADLINE_EXCEEDED"), ErrorTemporal),
        (httpx.Response(502, text="<html>Bad Gateway</html>"), ErrorTemporal),
        (respuesta_de_error(400, status="INVALID_ARGUMENT"), ErrorNoRecuperable),
        (respuesta_de_error(400, status="FAILED_PRECONDITION"), ErrorNoRecuperable),
        (respuesta_de_error(401, code="authentication"), ErrorNoRecuperable),
        (respuesta_de_error(403, status="PERMISSION_DENIED"), ErrorNoRecuperable),
        (respuesta_de_error(404, status="NOT_FOUND"), ErrorNoRecuperable),
    ],
)
def test_codigos_de_estado_se_traducen(respuesta, excepcion):
    error = error_al_generar(respuesta)

    assert type(error) is excepcion
    assert str(respuesta.status_code) in str(error)


def test_prompt_bloqueado_es_contenido_bloqueado():
    cuerpo = {"promptFeedback": {"blockReason": "SAFETY"}}

    error = error_al_generar(httpx.Response(200, json=cuerpo))

    assert type(error) is ContenidoBloqueado


@pytest.mark.parametrize("motivo", ["SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"])
def test_respuesta_bloqueada_es_contenido_bloqueado(motivo):
    cuerpo = {"candidates": [{"finishReason": motivo}]}

    error = error_al_generar(httpx.Response(200, json=cuerpo))

    assert type(error) is ContenidoBloqueado
    assert motivo in str(error)


@pytest.mark.parametrize(
    "cuerpo",
    [
        {"candidates": [{"content": {"parts": [{"text": "  "}]}, "finishReason": "STOP"}]},
        {"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]},
        {"candidates": [{"finishReason": "RECITATION"}]},
    ],
)
def test_texto_vacio_sin_bloqueo_es_error_temporal(cuerpo):
    assert type(error_al_generar(httpx.Response(200, json=cuerpo))) is ErrorTemporal


@pytest.mark.parametrize(
    "respuesta",
    [
        httpx.Response(200, json={}),
        httpx.Response(200, json={"candidates": []}),
        httpx.Response(200, json={"candidates": ["texto"]}),
        httpx.Response(200, json={"candidates": [{"content": "texto"}]}),
        httpx.Response(200, json={"candidates": [{"content": {"parts": "texto"}}]}),
        httpx.Response(200, json=["lista"]),
        httpx.Response(200, text="esto no es JSON"),
    ],
)
def test_estructura_inesperada_es_error_temporal(respuesta):
    assert type(error_al_generar(respuesta)) is ErrorTemporal


@pytest.mark.parametrize(
    "falla",
    [
        httpx.ReadTimeout(f"tiempo agotado: {CONSULTA}"),
        httpx.ConnectTimeout("tiempo agotado"),
        httpx.ConnectError("sin conexión"),
        httpx.ReadError("conexión cortada"),
    ],
)
def test_timeout_y_error_de_conexion_son_error_temporal(falla):
    assert type(error_al_generar(falla)) is ErrorTemporal


def test_corta_al_superar_el_tiempo_maximo_total():
    async def manejador_lento(solicitud: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1)
        return respuesta_ok()

    proveedor = ProveedorGemini(
        api_key=CLAVE,
        modelo=MODELO,
        timeout_segundos=0.05,
        cliente=cliente_simulado(manejador_lento),
    )

    with pytest.raises(ErrorTemporal):
        asyncio.run(proveedor.generar(crear_solicitud()))


def test_clave_que_no_admite_un_encabezado_es_error_no_recuperable():
    proveedor = ProveedorGemini(
        api_key="clave-ñ-inválida",
        modelo=MODELO,
        timeout_segundos=5,
        cliente=cliente_simulado(Captura(respuesta_ok())),
    )

    with pytest.raises(ErrorNoRecuperable) as info:
        asyncio.run(proveedor.generar(crear_solicitud()))

    assert "clave-ñ-inválida" not in str(info.value)


def test_repr_no_expone_la_clave():
    proveedor = ProveedorGemini(api_key=CLAVE, modelo=MODELO, timeout_segundos=5)

    assert CLAVE not in repr(proveedor)
    assert CLAVE not in str(proveedor)
    assert MODELO in repr(proveedor)
    asyncio.run(proveedor.aclose())


def test_aclose_no_cierra_un_cliente_inyectado():
    cliente = cliente_simulado(Captura(respuesta_ok()))
    proveedor = ProveedorGemini(api_key=CLAVE, modelo=MODELO, timeout_segundos=5, cliente=cliente)

    asyncio.run(proveedor.aclose())

    assert not cliente.is_closed


def test_falla_de_certificado_se_informa_en_el_mensaje():
    falla = httpx.ConnectError("certificate verify failed")
    falla.__cause__ = ssl.SSLCertVerificationError("certificate verify failed")

    error = error_al_generar(falla)

    assert type(error) is ErrorTemporal
    assert "certificado TLS" in str(error)
