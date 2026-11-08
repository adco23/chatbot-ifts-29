"""Pruebas de `EmbeddingsCloudflare`, sin red: las respuestas las da `httpx.MockTransport`."""

import json
from typing import Any

import httpx
import pytest

from app.providers import CuotaAgotada, ErrorNoRecuperable, ErrorProveedor, ErrorTemporal
from app.providers.embeddings import EmbeddingsCloudflare, normalizar_l2
from app.providers.embeddings.cloudflare import URL_BASE
from tests.unit.providers.ayudas import Captura, cliente_simulado
from tests.unit.providers.embeddings.ayudas import (
    CLAVE,
    CONSULTA,
    CUENTA,
    DOCUMENTOS,
    afirmar_normalizados,
    afirmar_sin_datos_sensibles,
    ejecutar,
    vector_crudo,
)

MODELO = "@cf/baai/bge-m3"
DIMENSION = 4


def crear(manejador: Any, **cambios: Any) -> EmbeddingsCloudflare:
    valores: dict[str, Any] = {
        "api_key": CLAVE,
        "account_id": CUENTA,
        "modelo": MODELO,
        "dimension": DIMENSION,
        "timeout_segundos": 5,
        "cliente": cliente_simulado(manejador),
    }
    return EmbeddingsCloudflare(**(valores | cambios))


def respuesta_ok(vectores: Any) -> httpx.Response:
    """Sobre de éxito de la API REST de Cloudflare."""
    forma = [len(vectores), len(vectores[0])] if vectores else [0, 0]
    cuerpo = {
        "result": {"shape": forma, "data": vectores},
        "success": True,
        "errors": [],
        "messages": [],
    }
    return httpx.Response(200, json=cuerpo)


def respuesta_de_error(estado: int, codigo: int = 0) -> httpx.Response:
    # El mensaje repite la consulta, como podría hacerlo el proveedor real.
    cuerpo = {
        "result": None,
        "success": False,
        "errors": [{"code": codigo, "message": f"Detalle: {CONSULTA}"}],
        "messages": [],
    }
    return httpx.Response(estado, json=cuerpo)


def vector_de(texto: str) -> list[float]:
    """Vector sin normalizar que identifica a un texto por su largo."""
    return [float(len(texto)), 1.0, 0.0, 0.0]


class PorLote:
    """Manejador que responde cada lote con un vector por texto y guarda las solicitudes."""

    def __init__(self) -> None:
        self.lotes: list[list[str]] = []

    def __call__(self, solicitud: httpx.Request) -> httpx.Response:
        textos = json.loads(solicitud.content)["text"]
        self.lotes.append(textos)
        return respuesta_ok([vector_de(texto) for texto in textos])


def error_en_consulta(respuesta: httpx.Response | Exception) -> ErrorProveedor:
    with pytest.raises(ErrorProveedor) as info:
        ejecutar(crear(Captura(respuesta)).embed_query(CONSULTA))
    assert info.value.proveedor == "cloudflare"
    afirmar_sin_datos_sensibles(info.value)
    return info.value


def error_en_documentos(respuesta: httpx.Response | Exception) -> ErrorProveedor:
    with pytest.raises(ErrorProveedor) as info:
        ejecutar(crear(Captura(respuesta)).embed_documents(DOCUMENTOS))
    assert info.value.proveedor == "cloudflare"
    afirmar_sin_datos_sensibles(info.value)
    return info.value


def test_la_consulta_envia_el_texto_y_devuelve_el_vector_normalizado():
    captura = Captura(respuesta_ok([[3.0, 4.0, 0.0, 0.0]]))

    vector = ejecutar(crear(captura).embed_query(CONSULTA))

    assert vector == pytest.approx([0.6, 0.8, 0.0, 0.0])
    assert captura.ultima.method == "POST"
    assert str(captura.ultima.url) == f"{URL_BASE}/accounts/{CUENTA}/ai/run/@cf/baai/bge-m3"
    assert captura.cuerpo == {"text": [CONSULTA]}


def test_los_documentos_van_en_un_lote_y_conservan_el_orden():
    crudos = [vector_crudo(DIMENSION, semilla) for semilla in (1.0, 2.5, -3.0)]
    captura = Captura(respuesta_ok(crudos))

    vectores = ejecutar(crear(captura).embed_documents(DOCUMENTOS))

    assert captura.cuerpo == {"text": DOCUMENTOS}
    assert vectores == [pytest.approx(normalizar_l2(crudo)) for crudo in crudos]


def test_el_token_va_por_encabezado_y_no_en_la_url():
    captura = Captura(respuesta_ok([vector_crudo(DIMENSION)]))

    ejecutar(crear(captura).embed_query(CONSULTA))

    assert captura.ultima.headers["Authorization"] == f"Bearer {CLAVE}"
    assert CLAVE not in str(captura.ultima.url)


def test_los_vectores_se_devuelven_normalizados_y_de_1024_dimensiones():
    dimension = 1024
    captura = Captura(respuesta_ok([vector_crudo(dimension, semilla) for semilla in (1.0, 0.01)]))

    vectores = ejecutar(crear(captura, dimension=dimension).embed_documents(DOCUMENTOS[:2]))

    afirmar_normalizados(vectores, dimension)


def test_los_documentos_se_parten_en_lotes_y_conservan_el_orden():
    textos = ["a", "bb", "ccc", "dddd", "eeeee"]
    manejador = PorLote()

    vectores = ejecutar(crear(manejador, lote_maximo=2).embed_documents(textos))

    assert manejador.lotes == [["a", "bb"], ["ccc", "dddd"], ["eeeee"]]
    assert vectores == [pytest.approx(normalizar_l2(vector_de(texto))) for texto in textos]


def test_sin_documentos_no_se_hace_ninguna_llamada():
    manejador = PorLote()

    assert ejecutar(crear(manejador).embed_documents([])) == []
    assert manejador.lotes == []


def test_un_texto_vacio_lanza_value_error_sin_llamar_al_proveedor():
    manejador = PorLote()
    proveedor = crear(manejador)

    with pytest.raises(ValueError):
        ejecutar(proveedor.embed_documents([DOCUMENTOS[0], " "]))
    with pytest.raises(ValueError):
        ejecutar(proveedor.embed_query(""))

    assert manejador.lotes == []


def test_la_consulta_y_los_documentos_usan_tiempos_maximos_distintos():
    consulta = Captura(respuesta_ok([vector_crudo(DIMENSION)]))
    documentos = Captura(respuesta_ok([vector_crudo(DIMENSION)]))

    ejecutar(crear(consulta, timeout_documentos_segundos=9).embed_query(CONSULTA))
    ejecutar(crear(documentos, timeout_documentos_segundos=9).embed_documents(DOCUMENTOS[:1]))

    assert consulta.ultima.extensions["timeout"]["read"] == 5
    assert documentos.ultima.extensions["timeout"]["read"] == 9


def test_la_cuenta_se_codifica_y_el_modelo_conserva_sus_barras():
    captura = Captura(respuesta_ok([vector_crudo(DIMENSION)]))

    ejecutar(crear(captura, account_id="cuenta/rara", modelo="@cf/otro/modelo").embed_query("x"))

    assert (
        captura.ultima.url.raw_path == b"/client/v4/accounts/cuenta%2Frara/ai/run/@cf/otro/modelo"
    )


@pytest.mark.parametrize(
    ("respuesta", "excepcion"),
    [
        # 3036: se agotó la asignación gratuita diaria. 3040: sin capacidad.
        (respuesta_de_error(429, codigo=3036), CuotaAgotada),
        (respuesta_de_error(429, codigo=3040), CuotaAgotada),
        (httpx.Response(429), CuotaAgotada),
        (respuesta_de_error(500), ErrorTemporal),
        (respuesta_de_error(502), ErrorTemporal),
        (respuesta_de_error(503), ErrorTemporal),
        (httpx.Response(408), ErrorTemporal),
        (respuesta_de_error(400, codigo=5007), ErrorNoRecuperable),
        (respuesta_de_error(401, codigo=10000), ErrorNoRecuperable),
        (respuesta_de_error(403, codigo=3023), ErrorNoRecuperable),
        (respuesta_de_error(404, codigo=3042), ErrorNoRecuperable),
    ],
)
def test_traduccion_de_estados_de_error(respuesta, excepcion):
    assert type(error_en_consulta(respuesta)) is excepcion
    assert type(error_en_documentos(respuesta)) is excepcion


@pytest.mark.parametrize(
    "falla",
    [
        httpx.ReadTimeout("tiempo agotado"),
        httpx.ConnectTimeout("tiempo agotado"),
        httpx.ConnectError("sin conexión"),
    ],
)
def test_tiempo_agotado_o_falla_de_conexion_es_error_temporal(falla):
    assert type(error_en_consulta(falla)) is ErrorTemporal


@pytest.mark.parametrize(
    "respuesta",
    [
        httpx.Response(200, json={}),
        httpx.Response(200, json={"result": None, "success": True}),
        httpx.Response(200, json={"result": {"data": "no es una lista"}, "success": True}),
        httpx.Response(200, json={"result": {"data": [[1.0, "dos", 3.0, 4.0]]}, "success": True}),
        httpx.Response(200, json={"result": {"data": [[0.0, 0.0, 0.0, 0.0]]}, "success": True}),
        # El sobre informa una falla aunque el estado sea de éxito.
        httpx.Response(200, json={"result": {"data": [[3.0, 4.0, 0.0, 0.0]]}, "success": False}),
        httpx.Response(200, text="no es JSON"),
    ],
    ids=[
        "vacio",
        "sin-resultado",
        "datos-no-lista",
        "valor-no-numerico",
        "vector-nulo",
        "sobre-con-falla",
        "no-json",
    ],
)
def test_respuesta_con_estructura_inesperada_es_error_temporal(respuesta):
    assert type(error_en_consulta(respuesta)) is ErrorTemporal


@pytest.mark.parametrize("cantidad", [len(DOCUMENTOS) - 1, len(DOCUMENTOS) + 1])
def test_cantidad_de_vectores_distinta_a_la_de_textos_es_error_temporal(cantidad):
    respuesta = respuesta_ok([vector_crudo(DIMENSION)] * cantidad)

    assert type(error_en_documentos(respuesta)) is ErrorTemporal


@pytest.mark.parametrize("recibida", [3, 768])
def test_vector_de_otra_dimension_es_error_de_configuracion(recibida):
    error = error_en_consulta(respuesta_ok([vector_crudo(recibida)]))

    assert type(error) is ErrorNoRecuperable
    assert str(recibida) in str(error)
    assert str(DIMENSION) in str(error)


def test_ni_el_token_ni_la_cuenta_aparecen_en_repr():
    proveedor = crear(Captura(respuesta_ok([vector_crudo(DIMENSION)])))

    assert (proveedor.nombre, proveedor.modelo, proveedor.dimension) == ("cloudflare", MODELO, 4)
    assert CLAVE not in repr(proveedor)
    assert CUENTA not in repr(proveedor)
