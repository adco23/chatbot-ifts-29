"""Pruebas de `EmbeddingsGemini`, sin red: las respuestas las da `httpx.MockTransport`."""

import json
from typing import Any

import httpx
import pytest

from app.providers import CuotaAgotada, ErrorNoRecuperable, ErrorProveedor, ErrorTemporal
from app.providers.embeddings import EmbeddingsGemini, normalizar_l2
from app.providers.gemini import URL_BASE
from tests.unit.providers.ayudas import Captura, cliente_simulado
from tests.unit.providers.embeddings.ayudas import (
    CLAVE,
    CONSULTA,
    DOCUMENTOS,
    afirmar_normalizados,
    afirmar_sin_datos_sensibles,
    ejecutar,
    vector_crudo,
)

MODELO = "gemini-embedding-de-prueba"
DIMENSION = 4


def crear(manejador: Any, **cambios: Any) -> EmbeddingsGemini:
    valores: dict[str, Any] = {
        "api_key": CLAVE,
        "modelo": MODELO,
        "dimension": DIMENSION,
        "timeout_segundos": 5,
        "cliente": cliente_simulado(manejador),
    }
    return EmbeddingsGemini(**(valores | cambios))


def respuesta_consulta(valores: Any = None) -> httpx.Response:
    valores = [3.0, 4.0, 0.0, 0.0] if valores is None else valores
    return httpx.Response(200, json={"embedding": {"values": valores}})


def respuesta_documentos(vectores: list[Any]) -> httpx.Response:
    return httpx.Response(200, json={"embeddings": [{"values": vector} for vector in vectores]})


def respuesta_de_error(estado: int, **error: Any) -> httpx.Response:
    # El mensaje repite la consulta, como podría hacerlo el proveedor real.
    return httpx.Response(estado, json={"error": {"message": f"Detalle: {CONSULTA}", **error}})


def vector_de(texto: str) -> list[float]:
    """Vector sin normalizar que identifica a un texto por su largo."""
    return [float(len(texto)), 1.0, 0.0, 0.0]


class PorLote:
    """Manejador que responde cada lote con un vector por texto y guarda las solicitudes."""

    def __init__(self) -> None:
        self.lotes: list[list[str]] = []

    def __call__(self, solicitud: httpx.Request) -> httpx.Response:
        pedidos = json.loads(solicitud.content)["requests"]
        textos = [pedido["content"]["parts"][0]["text"] for pedido in pedidos]
        self.lotes.append(textos)
        return respuesta_documentos([vector_de(texto) for texto in textos])


def error_en_consulta(respuesta: httpx.Response | Exception) -> ErrorProveedor:
    with pytest.raises(ErrorProveedor) as info:
        ejecutar(crear(Captura(respuesta)).embed_query(CONSULTA))
    assert info.value.proveedor == "gemini"
    afirmar_sin_datos_sensibles(info.value)
    return info.value


def error_en_documentos(respuesta: httpx.Response | Exception) -> ErrorProveedor:
    with pytest.raises(ErrorProveedor) as info:
        ejecutar(crear(Captura(respuesta)).embed_documents(DOCUMENTOS))
    assert info.value.proveedor == "gemini"
    afirmar_sin_datos_sensibles(info.value)
    return info.value


def test_la_consulta_usa_embed_content_con_la_tarea_de_consulta():
    captura = Captura(respuesta_consulta())

    vector = ejecutar(crear(captura).embed_query(CONSULTA))

    assert vector == pytest.approx([0.6, 0.8, 0.0, 0.0])
    assert str(captura.ultima.url) == f"{URL_BASE}/models/{MODELO}:embedContent"
    assert captura.cuerpo == {
        "model": f"models/{MODELO}",
        "content": {"parts": [{"text": CONSULTA}]},
        "taskType": "RETRIEVAL_QUERY",
        "outputDimensionality": DIMENSION,
    }


def test_los_documentos_usan_el_lote_con_la_tarea_de_documento():
    crudos = [vector_crudo(DIMENSION, semilla) for semilla in (1.0, 2.5, -3.0)]
    captura = Captura(respuesta_documentos(crudos))

    vectores = ejecutar(crear(captura).embed_documents(DOCUMENTOS))

    assert str(captura.ultima.url) == f"{URL_BASE}/models/{MODELO}:batchEmbedContents"
    assert captura.cuerpo == {
        "requests": [
            {
                "model": f"models/{MODELO}",
                "content": {"parts": [{"text": texto}]},
                "taskType": "RETRIEVAL_DOCUMENT",
                "outputDimensionality": DIMENSION,
            }
            for texto in DOCUMENTOS
        ]
    }
    assert vectores == [pytest.approx(normalizar_l2(crudo)) for crudo in crudos]


def test_la_clave_va_por_encabezado_y_no_en_la_url():
    captura = Captura(respuesta_consulta())

    ejecutar(crear(captura).embed_query(CONSULTA))

    assert captura.ultima.headers["x-goog-api-key"] == CLAVE
    assert CLAVE not in str(captura.ultima.url)


def test_los_vectores_se_devuelven_normalizados_y_de_la_dimension_configurada():
    dimension = 768
    captura = Captura(respuesta_consulta(vector_crudo(dimension)))

    vector = ejecutar(crear(captura, dimension=dimension).embed_query(CONSULTA))

    afirmar_normalizados([vector], dimension)
    assert captura.cuerpo["outputDimensionality"] == dimension


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


@pytest.mark.parametrize("textos", [["   "], [DOCUMENTOS[0], ""]])
def test_un_texto_vacio_lanza_value_error_sin_llamar_al_proveedor(textos):
    manejador = PorLote()
    proveedor = crear(manejador)

    with pytest.raises(ValueError):
        ejecutar(proveedor.embed_documents(textos))
    with pytest.raises(ValueError):
        ejecutar(proveedor.embed_query(textos[-1]))

    assert manejador.lotes == []


def test_la_consulta_y_los_documentos_usan_tiempos_maximos_distintos():
    consulta = Captura(respuesta_consulta())
    documentos = Captura(respuesta_documentos([vector_crudo(DIMENSION)]))

    ejecutar(crear(consulta, timeout_documentos_segundos=9).embed_query(CONSULTA))
    ejecutar(crear(documentos, timeout_documentos_segundos=9).embed_documents(DOCUMENTOS[:1]))

    assert consulta.ultima.extensions["timeout"]["read"] == 5
    assert documentos.ultima.extensions["timeout"]["read"] == 9


def test_sin_tiempo_de_documentos_se_usa_el_de_la_consulta():
    captura = Captura(respuesta_documentos([vector_crudo(DIMENSION)]))

    ejecutar(crear(captura).embed_documents(DOCUMENTOS[:1]))

    assert captura.ultima.extensions["timeout"]["read"] == 5


def test_el_modelo_se_codifica_en_la_url():
    captura = Captura(respuesta_consulta())

    ejecutar(crear(captura, modelo="carpeta/modelo raro").embed_query(CONSULTA))

    assert captura.ultima.url.raw_path == b"/v1beta/models/carpeta%2Fmodelo%20raro:embedContent"


@pytest.mark.parametrize(
    ("respuesta", "excepcion"),
    [
        (respuesta_de_error(429, status="RESOURCE_EXHAUSTED"), CuotaAgotada),
        (httpx.Response(429), CuotaAgotada),
        # Límite de uso informado en el cuerpo con otro estado.
        (respuesta_de_error(400, status="RESOURCE_EXHAUSTED"), CuotaAgotada),
        (respuesta_de_error(500, status="INTERNAL"), ErrorTemporal),
        (respuesta_de_error(503, status="UNAVAILABLE"), ErrorTemporal),
        (respuesta_de_error(504, status="DEADLINE_EXCEEDED"), ErrorTemporal),
        (httpx.Response(408), ErrorTemporal),
        (respuesta_de_error(400, status="INVALID_ARGUMENT"), ErrorNoRecuperable),
        (respuesta_de_error(400, status="FAILED_PRECONDITION"), ErrorNoRecuperable),
        (respuesta_de_error(403, status="PERMISSION_DENIED"), ErrorNoRecuperable),
        (respuesta_de_error(404, status="NOT_FOUND"), ErrorNoRecuperable),
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
        httpx.Response(200, json={"embedding": None}),
        httpx.Response(200, json={"embedding": {"values": "no es una lista"}}),
        httpx.Response(200, json={"embedding": {"values": [1.0, "dos", 3.0, 4.0]}}),
        httpx.Response(200, json={"embedding": {"values": [1.0, True, 3.0, 4.0]}}),
        httpx.Response(200, json={"embedding": {"values": [0.0, 0.0, 0.0, 0.0]}}),
        httpx.Response(200, json=["no", "es", "un", "objeto"]),
        httpx.Response(200, text="no es JSON"),
    ],
    ids=[
        "sin-embedding",
        "embedding-nulo",
        "valores-no-lista",
        "valor-no-numerico",
        "valor-logico",
        "vector-nulo",
        "json-no-objeto",
        "no-json",
    ],
)
def test_respuesta_de_consulta_con_estructura_inesperada_es_error_temporal(respuesta):
    assert type(error_en_consulta(respuesta)) is ErrorTemporal


@pytest.mark.parametrize(
    "respuesta",
    [
        httpx.Response(200, json={}),
        httpx.Response(200, json={"embeddings": "no es una lista"}),
        httpx.Response(
            200, json={"embeddings": [{"values": [1.0, 2.0, 3.0, 4.0]}, "no es objeto"]}
        ),
        # Menos vectores que textos: no se puede saber a cuál corresponde cada uno.
        respuesta_documentos([vector_crudo(DIMENSION)] * (len(DOCUMENTOS) - 1)),
        respuesta_documentos([vector_crudo(DIMENSION)] * (len(DOCUMENTOS) + 1)),
    ],
    ids=["sin-embeddings", "no-lista", "elemento-invalido", "faltan-vectores", "sobran-vectores"],
)
def test_respuesta_de_documentos_con_estructura_inesperada_es_error_temporal(respuesta):
    assert type(error_en_documentos(respuesta)) is ErrorTemporal


@pytest.mark.parametrize("recibida", [3, 5, 3072])
def test_vector_de_otra_dimension_es_error_de_configuracion(recibida):
    error = error_en_consulta(respuesta_consulta(vector_crudo(recibida)))

    assert type(error) is ErrorNoRecuperable
    assert str(recibida) in str(error)
    assert str(DIMENSION) in str(error)


def test_un_solo_vector_de_otra_dimension_invalida_todo_el_lote():
    vectores = [vector_crudo(DIMENSION), vector_crudo(DIMENSION + 1), vector_crudo(DIMENSION)]

    assert type(error_en_documentos(respuesta_documentos(vectores))) is ErrorNoRecuperable


def test_una_falla_en_un_lote_posterior_descarta_los_anteriores():
    respuestas = iter(
        [respuesta_documentos([vector_crudo(DIMENSION)] * 2), respuesta_de_error(503)]
    )

    with pytest.raises(ErrorTemporal):
        ejecutar(crear(lambda _: next(respuestas), lote_maximo=2).embed_documents(DOCUMENTOS))


@pytest.mark.parametrize("argumentos", [{"dimension": 0}, {"dimension": -1}, {"lote_maximo": 0}])
def test_parametros_invalidos_al_construir(argumentos):
    with pytest.raises(ValueError):
        crear(Captura(respuesta_consulta()), **argumentos)


def test_la_clave_no_aparece_en_repr():
    proveedor = crear(Captura(respuesta_consulta()))

    assert (proveedor.nombre, proveedor.modelo, proveedor.dimension) == ("gemini", MODELO, 4)
    assert CLAVE not in repr(proveedor)
    assert MODELO in repr(proveedor)
