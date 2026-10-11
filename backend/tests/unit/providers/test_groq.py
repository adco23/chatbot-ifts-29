"""Pruebas de `ProveedorGroq`, sin red: las respuestas las da `httpx.MockTransport`."""

import asyncio
from typing import Any

import httpx
import pytest

from app.providers import (
    ContenidoBloqueado,
    CuotaAgotada,
    ErrorNoRecuperable,
    ErrorProveedor,
    ErrorTemporal,
    ProveedorGroq,
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

MODELO = "modelo-groq-de-prueba"


def cuerpo_ok(texto: str | None = "Empiezan el 16 de marzo.", fin: str = "stop") -> dict[str, Any]:
    return {
        "model": "modelo-groq-efectivo",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": texto},
                "finish_reason": fin,
            }
        ],
    }


def respuesta_ok() -> httpx.Response:
    return httpx.Response(200, json=cuerpo_ok())


def respuesta_de_error(estado: int, **error: Any) -> httpx.Response:
    # El mensaje repite la consulta, como podría hacerlo el proveedor real.
    return httpx.Response(estado, json={"error": {"message": f"Detalle: {CONSULTA}", **error}})


def generar(
    respuesta: httpx.Response | Exception, solicitud: SolicitudLLM | None = None
) -> tuple[RespuestaLLM, Captura]:
    captura = Captura(respuesta)
    proveedor = ProveedorGroq(
        api_key=CLAVE, modelo=MODELO, timeout_segundos=5, cliente=cliente_simulado(captura)
    )
    return asyncio.run(proveedor.generar(solicitud or crear_solicitud())), captura


def error_al_generar(respuesta: httpx.Response | Exception) -> ErrorProveedor:
    with pytest.raises(ErrorProveedor) as info:
        generar(respuesta)
    assert info.value.proveedor == "groq"
    afirmar_sin_datos_sensibles(info.value)
    return info.value


def test_respuesta_exitosa():
    respuesta, _ = generar(respuesta_ok())

    assert respuesta == RespuestaLLM(
        texto="Empiezan el 16 de marzo.", proveedor="groq", modelo="modelo-groq-efectivo"
    )


def test_sin_modelo_en_la_respuesta_informa_el_configurado():
    cuerpo = cuerpo_ok()
    del cuerpo["model"]

    respuesta, _ = generar(httpx.Response(200, json=cuerpo))

    assert respuesta.modelo == MODELO


def test_la_solicitud_separa_instrucciones_y_mensaje_de_usuario():
    solicitud = crear_solicitud()

    _, captura = generar(respuesta_ok(), solicitud)

    assert captura.ultima.method == "POST"
    assert str(captura.ultima.url).endswith("/chat/completions")
    assert captura.cuerpo == {
        "model": MODELO,
        "messages": [
            {"role": "system", "content": INSTRUCCIONES},
            {"role": "user", "content": armar_mensaje_usuario(solicitud)},
        ],
    }


def test_sin_instrucciones_no_envia_el_mensaje_de_sistema():
    _, captura = generar(respuesta_ok(), crear_solicitud(instrucciones_sistema=""))

    assert [mensaje["role"] for mensaje in captura.cuerpo["messages"]] == ["user"]


@pytest.mark.parametrize(
    ("opciones", "esperado"),
    [
        (
            {"temperatura": 0.2, "max_tokens": 512},
            {"temperature": 0.2, "max_completion_tokens": 512},
        ),
        ({"temperatura": 0.0}, {"temperature": 0.0}),
        ({"max_tokens": 64}, {"max_completion_tokens": 64}),
    ],
)
def test_aplica_temperatura_y_maximo_de_tokens_solo_si_se_indican(opciones, esperado):
    _, captura = generar(respuesta_ok(), crear_solicitud(**opciones))

    opcionales = {
        campo: valor
        for campo, valor in captura.cuerpo.items()
        if campo not in ("model", "messages")
    }
    assert opcionales == esperado


def test_la_clave_viaja_por_encabezado_y_no_en_la_url():
    _, captura = generar(respuesta_ok())

    assert captura.ultima.headers["Authorization"] == f"Bearer {CLAVE}"
    assert CLAVE not in str(captura.ultima.url)
    assert captura.ultima.url.query == b""


@pytest.mark.parametrize(
    ("respuesta", "excepcion"),
    [
        (respuesta_de_error(429, type="tokens", code="rate_limit_exceeded"), CuotaAgotada),
        (httpx.Response(429, text="Too Many Requests"), CuotaAgotada),
        # La solicitud sola supera el límite de tokens por minuto.
        (respuesta_de_error(413, type="tokens", code="rate_limit_exceeded"), CuotaAgotada),
        (respuesta_de_error(413, type="invalid_request_error"), ErrorNoRecuperable),
        (respuesta_de_error(498), ErrorTemporal),
        (respuesta_de_error(500), ErrorTemporal),
        (httpx.Response(502, text="<html>Bad Gateway</html>"), ErrorTemporal),
        (respuesta_de_error(503), ErrorTemporal),
        (respuesta_de_error(400, type="invalid_request_error"), ErrorNoRecuperable),
        (respuesta_de_error(401, type="invalid_request_error"), ErrorNoRecuperable),
        (respuesta_de_error(403), ErrorNoRecuperable),
        (respuesta_de_error(404, code="model_not_found"), ErrorNoRecuperable),
        (respuesta_de_error(422), ErrorNoRecuperable),
    ],
)
def test_codigos_de_estado_se_traducen(respuesta, excepcion):
    error = error_al_generar(respuesta)

    assert type(error) is excepcion
    assert str(respuesta.status_code) in str(error)


def test_respuesta_filtrada_es_contenido_bloqueado():
    respuesta = httpx.Response(200, json=cuerpo_ok(texto="", fin="content_filter"))

    assert type(error_al_generar(respuesta)) is ContenidoBloqueado


@pytest.mark.parametrize(
    "cuerpo",
    [cuerpo_ok(texto=""), cuerpo_ok(texto="  \n"), cuerpo_ok(texto=None, fin="length")],
)
def test_texto_vacio_sin_bloqueo_es_error_temporal(cuerpo):
    assert type(error_al_generar(httpx.Response(200, json=cuerpo))) is ErrorTemporal


@pytest.mark.parametrize(
    "respuesta",
    [
        httpx.Response(200, json={}),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": ["texto"]}),
        httpx.Response(200, json={"choices": [{"finish_reason": "stop"}]}),
        httpx.Response(200, json={"choices": [{"message": {"content": ["texto"]}}]}),
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

    proveedor = ProveedorGroq(
        api_key=CLAVE,
        modelo=MODELO,
        timeout_segundos=0.05,
        cliente=cliente_simulado(manejador_lento),
    )

    with pytest.raises(ErrorTemporal):
        asyncio.run(proveedor.generar(crear_solicitud()))


def test_repr_no_expone_la_clave():
    proveedor = ProveedorGroq(api_key=CLAVE, modelo=MODELO, timeout_segundos=5)

    assert CLAVE not in repr(proveedor)
    assert CLAVE not in str(proveedor)
    assert MODELO in repr(proveedor)
    asyncio.run(proveedor.aclose())
