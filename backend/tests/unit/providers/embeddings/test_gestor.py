"""Pruebas de `GestorEmbeddings`, con proveedores simulados en lugar de los reales."""

import logging
import time
from typing import Any

import httpx
import pytest

from app.providers import CuotaAgotada, ErrorNoRecuperable, ErrorProveedor, ErrorTemporal
from app.providers.embeddings import (
    EmbeddingsCloudflare,
    EmbeddingsGemini,
    EmbeddingsNoDisponibles,
    GestorEmbeddings,
    ProveedorEmbeddings,
    SinProveedoresDeEmbeddings,
)
from app.providers.embeddings.falso import MODELO_FALSO, EmbeddingsFalso, vector_falso
from tests.unit.providers.ayudas import cliente_simulado
from tests.unit.providers.embeddings.ayudas import (
    CONSULTA,
    DOCUMENTOS,
    RelojFalso,
    afirmar_normalizados,
    crear_gestor,
    ejecutar,
    vector_crudo,
)

DIMENSION_GEMINI = 768
DIMENSION_CLOUDFLARE = 1024


def gemini_falso(*resultados: ErrorProveedor | None, **opciones: Any) -> EmbeddingsFalso:
    return EmbeddingsFalso(
        resultados or (None,), nombre="gemini", dimension=DIMENSION_GEMINI, **opciones
    )


def cloudflare_falso(*resultados: ErrorProveedor | None, **opciones: Any) -> EmbeddingsFalso:
    return EmbeddingsFalso(
        resultados or (None,), nombre="cloudflare", dimension=DIMENSION_CLOUDFLARE, **opciones
    )


class ProveedorQueTarda(EmbeddingsFalso):
    """Proveedor falso que hace avanzar el reloj simulado en cada llamada."""

    def __init__(self, reloj: RelojFalso, tarda: float, *resultados: Any, **opciones: Any) -> None:
        super().__init__(resultados or (None,), **opciones)
        self._reloj = reloj
        self._tarda = tarda

    async def _responder(self, textos: list[str]) -> list[list[float]]:
        self._reloj.ahora += self._tarda
        return await super()._responder(textos)


# --- Camino feliz ---


def test_responde_el_principal_y_no_se_llama_al_respaldo():
    principal, respaldo = gemini_falso(), cloudflare_falso()

    resultado = ejecutar(crear_gestor([principal, respaldo]).embed_query(CONSULTA))

    assert resultado.proveedor == "gemini"
    assert resultado.modelo == MODELO_FALSO
    assert resultado.dimension == DIMENSION_GEMINI
    assert resultado.vector == vector_falso(CONSULTA, DIMENSION_GEMINI, "gemini")
    assert (principal.llamadas, respaldo.llamadas) == (1, 0)


def test_los_documentos_conservan_el_orden_y_salen_del_principal():
    principal, respaldo = gemini_falso(), cloudflare_falso()

    resultado = ejecutar(crear_gestor([principal, respaldo]).embed_documents(DOCUMENTOS))

    assert resultado.proveedor == "gemini"
    assert resultado.vectores == [
        vector_falso(texto, DIMENSION_GEMINI, "gemini") for texto in DOCUMENTOS
    ]
    assert (principal.llamadas, respaldo.llamadas) == (1, 0)


def test_el_orden_de_la_lista_decide_quien_es_el_principal():
    gemini, cloudflare = gemini_falso(), cloudflare_falso()

    resultado = ejecutar(crear_gestor([cloudflare, gemini]).embed_query(CONSULTA))

    assert resultado.proveedor == "cloudflare"
    assert (cloudflare.llamadas, gemini.llamadas) == (1, 0)


# --- Respaldo ---


@pytest.mark.parametrize(
    "falla",
    [
        ErrorTemporal("El proveedor no respondió en 2 s."),
        CuotaAgotada("El proveedor respondió con el estado HTTP 429."),
        ErrorTemporal("El proveedor respondió con el estado HTTP 503."),
    ],
    ids=["timeout", "429", "5xx"],
)
@pytest.mark.parametrize("operacion", ["consulta", "documentos"])
def test_ante_timeout_429_o_5xx_responde_el_respaldo(falla, operacion):
    principal, respaldo = gemini_falso(falla), cloudflare_falso()
    gestor = crear_gestor([principal, respaldo], RelojFalso(), reintentos=0)

    if operacion == "consulta":
        resultado = ejecutar(gestor.embed_query(CONSULTA))
        vectores = [resultado.vector]
    else:
        resultado = ejecutar(gestor.embed_documents(DOCUMENTOS))
        vectores = resultado.vectores

    assert resultado.proveedor == "cloudflare"
    assert resultado.dimension == DIMENSION_CLOUDFLARE
    afirmar_normalizados(vectores, DIMENSION_CLOUDFLARE)
    assert (principal.llamadas, respaldo.llamadas) == (1, 1)


def test_el_respaldo_tambien_funciona_con_el_orden_invertido():
    principal, respaldo = cloudflare_falso(CuotaAgotada()), gemini_falso()

    resultado = ejecutar(crear_gestor([principal, respaldo]).embed_query(CONSULTA))

    assert resultado.proveedor == "gemini"
    assert resultado.dimension == DIMENSION_GEMINI
    assert (principal.llamadas, respaldo.llamadas) == (1, 1)


def test_error_de_configuracion_se_registra_como_tal_y_responde_el_respaldo(caplog):
    caplog.set_level(logging.DEBUG)
    principal = gemini_falso(ErrorNoRecuperable("El proveedor respondió con el estado HTTP 401."))
    respaldo = cloudflare_falso()

    resultado = ejecutar(crear_gestor([principal, respaldo], reintentos=3).embed_query(CONSULTA))

    assert resultado.proveedor == "cloudflare"
    # No se reintenta: una credencial o una solicitud inválida no se arreglan sola.
    assert (principal.llamadas, respaldo.llamadas) == (1, 1)
    errores = [registro for registro in caplog.records if registro.levelno == logging.ERROR]
    assert len(errores) == 1
    mensaje = errores[0].getMessage()
    assert "Error de configuración" in mensaje
    assert "gemini" in mensaje
    assert "ErrorNoRecuperable" in mensaje


def test_los_documentos_nunca_mezclan_vectores_de_dos_proveedores():
    principal, respaldo = gemini_falso(ErrorTemporal()), cloudflare_falso()
    gestor = crear_gestor([principal, respaldo], RelojFalso(), reintentos=0)

    resultado = ejecutar(gestor.embed_documents(DOCUMENTOS))

    assert resultado.proveedor == "cloudflare"
    assert resultado.vectores == [
        vector_falso(texto, DIMENSION_CLOUDFLARE, "cloudflare") for texto in DOCUMENTOS
    ]
    # El respaldo recibe el conjunto completo, no solo lo que le faltó al principal.
    assert respaldo.ultimos_textos == DOCUMENTOS


def test_excepcion_fuera_del_contrato_se_trata_como_falla_temporal():
    class ProveedorRoto(ProveedorEmbeddings):
        nombre = "roto"
        modelo = "m"
        dimension = 3

        async def embed_documents(self, textos):
            raise RuntimeError(f"falló con: {textos}")

        async def embed_query(self, texto):
            raise RuntimeError(f"falló con: {texto}")

    respaldo = cloudflare_falso()
    gestor = crear_gestor([ProveedorRoto(), respaldo], RelojFalso())

    assert ejecutar(gestor.embed_query(CONSULTA)).proveedor == "cloudflare"

    with pytest.raises(EmbeddingsNoDisponibles) as info:
        ejecutar(crear_gestor([ProveedorRoto()], RelojFalso()).embed_query(CONSULTA))
    (error,) = info.value.errores
    assert type(error) is ErrorTemporal
    assert error.proveedor == "roto"
    # La excepción original no viaja encadenada: podría incluir el texto.
    assert CONSULTA not in str(error)
    assert error.__cause__ is None
    assert error.__suppress_context__


# --- Reintentos ---


def test_una_falla_temporal_se_reintenta_y_puede_responder_el_principal():
    reloj = RelojFalso()
    principal, respaldo = gemini_falso(ErrorTemporal(), None), cloudflare_falso()

    resultado = ejecutar(crear_gestor([principal, respaldo], reloj).embed_query(CONSULTA))

    assert resultado.proveedor == "gemini"
    assert (principal.llamadas, respaldo.llamadas) == (2, 0)
    assert reloj.esperas == [0.25]


def test_la_espera_entre_reintentos_es_exponencial_y_acotada():
    reloj = RelojFalso()
    principal, respaldo = gemini_falso(ErrorTemporal()), cloudflare_falso()
    gestor = crear_gestor(
        [principal, respaldo],
        reloj,
        reintentos=4,
        backoff_base_segundos=0.25,
        backoff_maximo_segundos=1.5,
    )

    resultado = ejecutar(gestor.embed_documents(DOCUMENTOS))

    assert resultado.proveedor == "cloudflare"
    # Un intento inicial más cuatro reintentos, y nunca más que eso.
    assert principal.llamadas == 5
    assert reloj.esperas == [0.25, 0.5, 1.0, 1.5]


def test_sin_reintentos_se_pasa_al_respaldo_al_primer_fallo():
    reloj = RelojFalso()
    principal, respaldo = gemini_falso(ErrorTemporal()), cloudflare_falso()

    ejecutar(crear_gestor([principal, respaldo], reloj, reintentos=0).embed_query(CONSULTA))

    assert (principal.llamadas, respaldo.llamadas) == (1, 1)
    assert reloj.esperas == []


@pytest.mark.parametrize("excepcion", [CuotaAgotada, ErrorNoRecuperable])
def test_cuota_agotada_y_error_de_configuracion_no_se_reintentan(excepcion):
    reloj = RelojFalso()
    principal, respaldo = gemini_falso(excepcion(), None), cloudflare_falso()

    resultado = ejecutar(
        crear_gestor([principal, respaldo], reloj, reintentos=3).embed_query(CONSULTA)
    )

    assert resultado.proveedor == "cloudflare"
    assert (principal.llamadas, respaldo.llamadas) == (1, 1)
    assert reloj.esperas == []


def test_el_respaldo_tambien_reintenta():
    reloj = RelojFalso()
    principal = gemini_falso(CuotaAgotada())
    respaldo = cloudflare_falso(ErrorTemporal(), None)

    resultado = ejecutar(crear_gestor([principal, respaldo], reloj).embed_query(CONSULTA))

    assert resultado.proveedor == "cloudflare"
    assert (principal.llamadas, respaldo.llamadas) == (1, 2)


# --- Fallan todos ---


@pytest.mark.parametrize("operacion", ["consulta", "documentos"])
def test_si_fallan_ambos_se_lanza_el_error_especifico_con_todos_los_errores(operacion):
    cuota, temporal = CuotaAgotada(), ErrorTemporal()
    gestor = crear_gestor(
        [gemini_falso(cuota), cloudflare_falso(temporal)], RelojFalso(), reintentos=0
    )

    with pytest.raises(EmbeddingsNoDisponibles) as info:
        if operacion == "consulta":
            ejecutar(gestor.embed_query(CONSULTA))
        else:
            ejecutar(gestor.embed_documents(DOCUMENTOS))

    assert info.value.errores == [cuota, temporal]
    assert "gemini: CuotaAgotada" in str(info.value)
    assert "cloudflare: ErrorTemporal" in str(info.value)


def test_dos_errores_de_configuracion_tambien_terminan_en_el_error_especifico():
    gestor = crear_gestor(
        [gemini_falso(ErrorNoRecuperable()), cloudflare_falso(ErrorNoRecuperable())]
    )

    with pytest.raises(EmbeddingsNoDisponibles) as info:
        ejecutar(gestor.embed_query(CONSULTA))

    assert [type(error) for error in info.value.errores] == [ErrorNoRecuperable] * 2
    assert [error.proveedor for error in info.value.errores] == ["gemini", "cloudflare"]


def test_el_error_especifico_no_se_confunde_con_el_de_un_proveedor():
    gestor = crear_gestor([gemini_falso(CuotaAgotada())])

    with pytest.raises(EmbeddingsNoDisponibles) as info:
        ejecutar(gestor.embed_query(CONSULTA))

    assert not isinstance(info.value, ErrorProveedor)


@pytest.mark.parametrize("operacion", ["consulta", "documentos", "sin-documentos"])
def test_sin_proveedores_se_informa_que_no_hay_ninguno_configurado(operacion):
    gestor = crear_gestor([])

    with pytest.raises(EmbeddingsNoDisponibles) as info:
        if operacion == "consulta":
            ejecutar(gestor.embed_query(CONSULTA))
        else:
            ejecutar(gestor.embed_documents(DOCUMENTOS if operacion == "documentos" else []))

    assert isinstance(info.value, SinProveedoresDeEmbeddings)
    assert info.value.errores == []
    assert str(info.value) == "No hay proveedores de embeddings configurados."


# --- Vectores ---


@pytest.mark.parametrize(
    ("falla_del_principal", "proveedor", "dimension"),
    [(None, "gemini", DIMENSION_GEMINI), (CuotaAgotada(), "cloudflare", DIMENSION_CLOUDFLARE)],
    ids=["principal", "respaldo"],
)
def test_los_vectores_estan_normalizados_y_tienen_la_dimension_de_su_proveedor(
    falla_del_principal, proveedor, dimension
):
    gestor = crear_gestor([gemini_falso(falla_del_principal), cloudflare_falso()])

    consulta = ejecutar(gestor.embed_query(CONSULTA))
    documentos = ejecutar(gestor.embed_documents(DOCUMENTOS))

    for resultado in (consulta, documentos):
        assert (resultado.proveedor, resultado.dimension) == (proveedor, dimension)
    assert len(documentos.vectores) == len(DOCUMENTOS)
    afirmar_normalizados([consulta.vector, *documentos.vectores], dimension)


def test_sin_documentos_no_se_llama_a_ningun_proveedor():
    principal, respaldo = gemini_falso(), cloudflare_falso()

    resultado = ejecutar(crear_gestor([principal, respaldo]).embed_documents([]))

    assert resultado.vectores == []
    assert resultado.proveedor == "gemini"
    assert (principal.llamadas, respaldo.llamadas) == (0, 0)


def test_un_texto_vacio_lanza_value_error_sin_llamar_a_ningun_proveedor():
    principal, respaldo = gemini_falso(), cloudflare_falso()
    gestor = crear_gestor([principal, respaldo])

    with pytest.raises(ValueError):
        ejecutar(gestor.embed_query("   "))
    with pytest.raises(ValueError):
        ejecutar(gestor.embed_documents([DOCUMENTOS[0], ""]))
    with pytest.raises(ValueError):
        ejecutar(gestor.embed_documents(CONSULTA))

    assert (principal.llamadas, respaldo.llamadas) == (0, 0)


# --- Presupuesto de tiempo ---


def test_la_falla_rapida_del_principal_deja_tiempo_para_reintentar():
    reloj = RelojFalso()
    principal = ProveedorQueTarda(reloj, 0.1, ErrorTemporal(), None, nombre="gemini")
    respaldo = cloudflare_falso()

    resultado = ejecutar(crear_gestor([principal, respaldo], reloj).embed_query(CONSULTA))

    assert resultado.proveedor == "gemini"
    assert principal.llamadas == 2


def test_no_se_reintenta_si_la_espera_agota_la_parte_del_proveedor():
    reloj = RelojFalso()
    # Con un presupuesto de 4 s y dos proveedores, al principal le tocan 2 s.
    principal = ProveedorQueTarda(reloj, 1.9, ErrorTemporal(), nombre="gemini")
    respaldo = cloudflare_falso()

    resultado = ejecutar(crear_gestor([principal, respaldo], reloj).embed_query(CONSULTA))

    assert resultado.proveedor == "cloudflare"
    assert (principal.llamadas, respaldo.llamadas) == (1, 1)
    assert reloj.esperas == []


def test_con_el_presupuesto_agotado_no_se_intenta_con_el_respaldo(caplog):
    caplog.set_level(logging.DEBUG)
    reloj = RelojFalso()
    principal = ProveedorQueTarda(reloj, 5.0, ErrorTemporal(), nombre="gemini")
    respaldo = cloudflare_falso()

    with pytest.raises(EmbeddingsNoDisponibles) as info:
        ejecutar(crear_gestor([principal, respaldo], reloj).embed_query(CONSULTA))

    assert [error.proveedor for error in info.value.errores] == ["gemini"]
    assert respaldo.llamadas == 0
    assert "Presupuesto de 4.00 s agotado" in caplog.text


def test_el_presupuesto_no_se_aplica_a_los_documentos():
    reloj = RelojFalso()
    principal = ProveedorQueTarda(reloj, 30.0, ErrorTemporal(), None, nombre="gemini")
    respaldo = cloudflare_falso()

    resultado = ejecutar(crear_gestor([principal, respaldo], reloj).embed_documents(DOCUMENTOS))

    # Aunque pasó mucho más que el presupuesto, el principal se reintenta y responde.
    assert resultado.proveedor == "gemini"
    assert principal.llamadas == 2


def test_un_principal_lento_no_deja_sin_tiempo_al_respaldo():
    lento = gemini_falso(latencia_segundos=5)
    respaldo = cloudflare_falso()
    gestor = crear_gestor([lento, respaldo], presupuesto_segundos=0.6)

    inicio = time.monotonic()
    resultado = ejecutar(gestor.embed_query(CONSULTA))
    duracion = time.monotonic() - inicio

    assert resultado.proveedor == "cloudflare"
    # El lento se corta al agotar su parte y no se reintenta.
    assert (lento.llamadas, respaldo.llamadas) == (1, 1)
    assert duracion < 3


def test_si_ambos_son_lentos_se_corta_al_llegar_al_presupuesto():
    lento, otro_lento = gemini_falso(latencia_segundos=5), cloudflare_falso(latencia_segundos=5)
    gestor = crear_gestor([lento, otro_lento], presupuesto_segundos=0.6)

    inicio = time.monotonic()
    with pytest.raises(EmbeddingsNoDisponibles) as info:
        ejecutar(gestor.embed_query(CONSULTA))
    duracion = time.monotonic() - inicio

    assert duracion < 3
    assert [(type(error), error.proveedor) for error in info.value.errores] == [
        (ErrorTemporal, "gemini"),
        (ErrorTemporal, "cloudflare"),
    ]


# --- Estado, registros y parámetros ---


def test_no_guarda_estado_entre_llamadas():
    principal = gemini_falso(CuotaAgotada(), None)
    gestor = crear_gestor([principal, cloudflare_falso()])

    assert ejecutar(gestor.embed_query(CONSULTA)).proveedor == "cloudflare"
    # La falla anterior no saca al principal de la lista.
    assert ejecutar(gestor.embed_query(CONSULTA)).proveedor == "gemini"


def test_los_registros_informan_proveedor_y_categoria_sin_el_contenido(caplog):
    caplog.set_level(logging.DEBUG)
    principal = gemini_falso(CuotaAgotada(f"detalle con {CONSULTA} y {DOCUMENTOS[0]}"))
    gestor = crear_gestor([principal, cloudflare_falso()])

    ejecutar(gestor.embed_query(CONSULTA))
    ejecutar(gestor.embed_documents(DOCUMENTOS))

    registros = "\n".join(registro.getMessage() for registro in caplog.records)
    assert "gemini" in registros
    assert "CuotaAgotada" in registros
    assert "cloudflare" in registros
    for dato in (CONSULTA, *DOCUMENTOS):
        assert dato not in registros
    assert all(registro.exc_info is None for registro in caplog.records)


@pytest.mark.parametrize(
    "proveedores",
    [
        [],
        [gemini_falso(ErrorTemporal(CONSULTA)), cloudflare_falso(CuotaAgotada(CONSULTA))],
        [gemini_falso(ErrorNoRecuperable(CONSULTA))],
    ],
    ids=["sin-proveedores", "fallan-ambos", "configuracion"],
)
def test_los_registros_de_fallas_no_incluyen_el_contenido(caplog, proveedores):
    caplog.set_level(logging.DEBUG)

    with pytest.raises(EmbeddingsNoDisponibles):
        ejecutar(crear_gestor(proveedores, RelojFalso()).embed_query(CONSULTA))

    assert caplog.records
    assert CONSULTA not in caplog.text


@pytest.mark.parametrize(
    "cambios",
    [
        {"reintentos": -1},
        {"backoff_base_segundos": 0},
        {"backoff_base_segundos": 2.0, "backoff_maximo_segundos": 1.0},
        {"presupuesto_segundos": 0},
    ],
)
def test_parametros_invalidos_al_construir(cambios):
    with pytest.raises(ValueError):
        crear_gestor([gemini_falso()], **cambios)


def test_expone_proveedores_y_parametros():
    principal, respaldo = gemini_falso(), cloudflare_falso()
    gestor = crear_gestor([principal, respaldo], reintentos=2, presupuesto_segundos=3.0)

    assert gestor.proveedores == (principal, respaldo)
    assert (gestor.reintentos, gestor.presupuesto_segundos) == (2, 3.0)
    assert (gestor.backoff_base_segundos, gestor.backoff_maximo_segundos) == (0.25, 1.0)


# --- Respaldo con los proveedores HTTP reales y respuestas simuladas ---


def gestor_http(respuesta_de_gemini: httpx.Response | Exception) -> GestorEmbeddings:
    """Gemini como principal, con la respuesta indicada, y Cloudflare sano de respaldo."""

    def gemini(_: httpx.Request) -> httpx.Response:
        if isinstance(respuesta_de_gemini, Exception):
            raise respuesta_de_gemini
        return respuesta_de_gemini

    def cloudflare(_: httpx.Request) -> httpx.Response:
        cuerpo = {"result": {"data": [vector_crudo(DIMENSION_CLOUDFLARE)]}, "success": True}
        return httpx.Response(200, json=cuerpo)

    principal = EmbeddingsGemini(
        api_key="clave-ficticia",
        modelo="gemini-embedding-001",
        dimension=DIMENSION_GEMINI,
        timeout_segundos=5,
        cliente=cliente_simulado(gemini),
    )
    respaldo = EmbeddingsCloudflare(
        api_key="token-ficticio",
        account_id="cuenta-ficticia",
        modelo="@cf/baai/bge-m3",
        dimension=DIMENSION_CLOUDFLARE,
        timeout_segundos=5,
        cliente=cliente_simulado(cloudflare),
    )
    return crear_gestor([principal, respaldo], RelojFalso())


@pytest.mark.parametrize(
    "respuesta_de_gemini",
    [
        httpx.ReadTimeout("tiempo agotado"),
        httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED"}}),
        httpx.Response(500),
        httpx.Response(503),
        httpx.Response(401),
        httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT"}}),
        # Gemini responde bien, pero con una dimensión que no es la configurada.
        httpx.Response(200, json={"embedding": {"values": vector_crudo(3072)}}),
    ],
    ids=["timeout", "429", "500", "503", "401", "400", "dimension-distinta"],
)
def test_con_proveedores_http_la_falla_de_gemini_termina_en_cloudflare(respuesta_de_gemini):
    resultado = ejecutar(gestor_http(respuesta_de_gemini).embed_query(CONSULTA))

    assert resultado.proveedor == "cloudflare"
    assert resultado.modelo == "@cf/baai/bge-m3"
    afirmar_normalizados([resultado.vector], DIMENSION_CLOUDFLARE)


def test_con_proveedores_http_responde_gemini_si_esta_sano():
    respuesta = httpx.Response(200, json={"embedding": {"values": vector_crudo(DIMENSION_GEMINI)}})

    resultado = ejecutar(gestor_http(respuesta).embed_query(CONSULTA))

    assert resultado.proveedor == "gemini"
    afirmar_normalizados([resultado.vector], DIMENSION_GEMINI)
