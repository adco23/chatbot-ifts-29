"""Indexación y CLI con Drive, embeddings y persistencia completamente simulados."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.rag import (
    DocumentoDrive,
    FragmentoParaIndexar,
    ResultadoIndexacionDocumento,
    ResultadoRecuperacion,
    ResultadoSincronizacion,
    ResumenIndexacion,
)
from app.providers.embeddings.base import ResultadoEmbeddings
from app.services import sincronizador
from app.services.validacion_embeddings_rag import ErrorEmbeddingIncompatible
from app.settings import DIMENSION_EMBEDDINGS_RAG, MODELO_EMBEDDINGS_RAG, Configuracion
from scripts import probar_rag

FECHA = datetime(2026, 10, 23, tzinfo=UTC)
SECCIONES = [
    {"section": "Ingreso", "content": "La inscripción se realiza en marzo."},
    {"section": "Atención", "content": "Secretaría atiende por la tarde."},
]


def vector() -> list[float]:
    return [1.0] + [0.0] * (DIMENSION_EMBEDDINGS_RAG - 1)


def resultado_embeddings(textos) -> ResultadoEmbeddings:
    return ResultadoEmbeddings(
        vectores=[vector() for _ in textos],
        proveedor="gemini",
        modelo=MODELO_EMBEDDINGS_RAG,
        dimension=DIMENSION_EMBEDDINGS_RAG,
    )


@pytest.fixture
def documento() -> DocumentoDrive:
    return DocumentoDrive(drive_id="drive-uno", titulo="Preguntas.pdf", tipo_mime="application/pdf")


@pytest.fixture
def config() -> Configuracion:
    return Configuracion(_env_file=None)


@pytest.fixture
def colaboradores(monkeypatch):
    consultar = MagicMock(return_value=None)
    descargar = MagicMock(return_value=b"documento-simulado")
    extraer = MagicMock(return_value=SECCIONES)
    persistir = MagicMock(
        side_effect=lambda documento, fragmentos: ResumenIndexacion(
            documento_id=7, fragmentos_indexados=len(fragmentos), indexado_en=FECHA
        )
    )
    gestor = SimpleNamespace(
        embed_documents=AsyncMock(side_effect=resultado_embeddings),
        embed_query=AsyncMock(),
        proveedores=(),
    )
    monkeypatch.setattr(sincronizador, "obtener_documento_por_drive_id", consultar)
    monkeypatch.setattr(sincronizador, "descargar_contenido_archivo", descargar)
    monkeypatch.setattr(sincronizador, "extraer_texto_desde_bytes", extraer)
    monkeypatch.setattr(sincronizador, "sync_document_chunks", persistir)
    return SimpleNamespace(
        consultar=consultar,
        descargar=descargar,
        extraer=extraer,
        persistir=persistir,
        gestor=gestor,
    )


def test_fragmentacion_preserva_texto_solapamiento_orden_y_metadatos():
    texto = "".join(f"{indice:04d}" for indice in range(700))
    chunks = sincronizador.split_text_into_chunks(texto, "Reglamento", "Ingreso")
    assert max(len(chunk.page_content) for chunk in chunks) == 1000
    assert all(0 < len(chunk.page_content) <= 1000 for chunk in chunks)
    for anterior, siguiente in zip(chunks, chunks[1:], strict=False):
        assert anterior.page_content[-200:] == siguiente.page_content[:200]
    reconstruido = chunks[0].page_content + "".join(
        chunk.page_content[200:] for chunk in chunks[1:]
    )
    assert reconstruido == texto
    assert all(
        chunk.metadata == {"document_title": "Reglamento", "section": "Ingreso"}
        for chunk in chunks
    )
    assert chunks == sincronizador.split_text_into_chunks(texto, "Reglamento", "Ingreso")


@pytest.mark.parametrize("texto", ["", "   ", "\n\t"])
def test_fragmentacion_en_blanco_no_genera_chunks(texto):
    assert sincronizador.split_text_into_chunks(texto, "Documento", "General") == []


@pytest.mark.parametrize("entrada", [{"archivo": "hash"}, "archivo", b"archivo", [dict()]])
def test_entrada_invalida_se_rechaza_antes_de_configuracion_o_io(
    entrada, colaboradores, monkeypatch
):
    obtener = MagicMock()
    monkeypatch.setattr(sincronizador, "obtener_configuracion", obtener)
    with pytest.raises(ValueError):
        asyncio.run(sincronizador.run_full_sync(entrada))
    obtener.assert_not_called()
    colaboradores.descargar.assert_not_called()


def test_seleccion_repetida_se_rechaza_antes_de_io(documento, config, colaboradores):
    with pytest.raises(ValueError):
        asyncio.run(sincronizador.run_full_sync([documento, documento], configuracion=config))
    colaboradores.descargar.assert_not_called()


def test_indexa_secciones_con_embed_documents_antes_de_persistir(documento, config, colaboradores):
    resultado = asyncio.run(
        sincronizador.run_full_sync([documento], gestor=colaboradores.gestor, configuracion=config)
    )
    assert (resultado.procesados, resultado.errores) == (1, 0)
    colaboradores.consultar.assert_called_once_with(documento.drive_id)
    colaboradores.descargar.assert_called_once_with(
        documento.drive_id, tipo_mime="application/pdf", estricto=True
    )
    colaboradores.extraer.assert_called_once_with(
        b"documento-simulado", "Preguntas.pdf", tipo_mime="application/pdf"
    )
    colaboradores.gestor.embed_documents.assert_awaited_once_with(
        [seccion["content"] for seccion in SECCIONES]
    )
    colaboradores.gestor.embed_query.assert_not_awaited()
    guardado, fragmentos = colaboradores.persistir.call_args.args
    assert guardado == documento
    assert [fragmento.orden for fragmento in fragmentos] == [1, 2]
    assert [fragmento.contenido for fragmento in fragmentos] == [s["content"] for s in SECCIONES]
    assert all(fragmento.vector == vector() for fragmento in fragmentos)


def test_reindexacion_explicita_repite_lote_y_reemplaza_sin_cache(documento, config, colaboradores):
    async def escenario():
        return [
            await sincronizador.run_full_sync(
                [documento], gestor=colaboradores.gestor, configuracion=config
            )
            for _ in range(2)
        ]

    resultados = asyncio.run(escenario())
    assert colaboradores.gestor.embed_documents.await_count == 2
    assert colaboradores.persistir.call_count == 2
    assert [resultado.resultados[0].resumen.documento_id for resultado in resultados] == [7, 7]
    assert [len(llamada.args[1]) for llamada in colaboradores.persistir.call_args_list] == [2, 2]


@pytest.mark.parametrize("fallo", ["cloudflare", "modelo", "cantidad", "dimension", "no_finito"])
def test_valida_lote_completo_y_aborta_antes_de_rpc(fallo, documento, config, colaboradores):
    resultado = resultado_embeddings(["uno", "dos"])
    if fallo == "cloudflare":
        resultado = ResultadoEmbeddings([[1.0] * 1024] * 2, "cloudflare", "bge-m3", 1024)
    elif fallo == "modelo":
        resultado = ResultadoEmbeddings(resultado.vectores, "gemini", "otro-modelo", 768)
    elif fallo == "cantidad":
        resultado = ResultadoEmbeddings(
            resultado.vectores[:1], "gemini", MODELO_EMBEDDINGS_RAG, 768
        )
    elif fallo == "dimension":
        resultado.vectores[-1].pop()
    else:
        resultado.vectores[-1][-1] = float("nan")
    colaboradores.gestor.embed_documents.side_effect = None
    colaboradores.gestor.embed_documents.return_value = resultado
    resumen = asyncio.run(
        sincronizador.run_full_sync([documento], gestor=colaboradores.gestor, configuracion=config)
    )
    assert resumen.resultados[0].error == "embeddings_incompatibles"
    assert resumen.procesados == 0
    colaboradores.persistir.assert_not_called()


@pytest.mark.parametrize(
    "secciones",
    [
        [],
        [{"section": "General", "content": "  "}],
        [{"section": "General", "content": None}],
    ],
)
def test_extraccion_sin_texto_no_genera_embeddings_o_escrituras(
    secciones, documento, config, colaboradores
):
    colaboradores.extraer.return_value = secciones
    resumen = asyncio.run(
        sincronizador.run_full_sync([documento], gestor=colaboradores.gestor, configuracion=config)
    )
    assert resumen.resultados[0].error == "extraccion_fallida"
    colaboradores.gestor.embed_documents.assert_not_awaited()
    colaboradores.persistir.assert_not_called()


def test_un_error_no_interrumpe_documentos_restantes_ni_filtra_payload(
    documento, config, colaboradores, caplog
):
    privado = "contenido-privado-que-no-debe-registrarse"
    colaboradores.descargar.side_effect = [RuntimeError(privado), b"segundo"]
    otro = documento.model_copy(update={"drive_id": "drive-dos", "titulo": "Otro.pdf"})
    resultado = asyncio.run(
        sincronizador.run_full_sync(
            [documento, otro], gestor=colaboradores.gestor, configuracion=config
        )
    )
    assert (resultado.procesados, resultado.errores) == (1, 1)
    assert resultado.resultados[0].error == "descarga_fallida"
    assert resultado.resultados[1].resumen.documento_id == 7
    assert privado not in caplog.text
    assert privado not in resultado.model_dump_json()


def test_fallo_rpc_es_error_y_no_exito(documento, config, colaboradores):
    colaboradores.persistir.side_effect = RuntimeError("payload privado")
    resultado = asyncio.run(
        sincronizador.run_full_sync([documento], gestor=colaboradores.gestor, configuracion=config)
    )
    assert (resultado.procesados, resultado.errores) == (0, 1)
    assert resultado.resultados[0].error == "persistencia_fallida"
    assert resultado.resultados[0].resumen is None


def test_fallo_lectura_documento_aborta_antes_de_drive(documento, config, colaboradores):
    colaboradores.consultar.side_effect = RuntimeError("detalle privado")
    resultado = asyncio.run(
        sincronizador.run_full_sync([documento], gestor=colaboradores.gestor, configuracion=config)
    )
    assert resultado.resultados[0].error == "consulta_documento_fallida"
    colaboradores.descargar.assert_not_called()
    colaboradores.persistir.assert_not_called()


def test_configuracion_incompatible_aborta_antes_de_drive(documento, config, colaboradores):
    invalida = config.model_copy(update={"embedding_primary": "cloudflare"})
    with pytest.raises(ErrorEmbeddingIncompatible):
        asyncio.run(
            sincronizador.run_full_sync(
                [documento], gestor=colaboradores.gestor, configuracion=invalida
            )
        )
    colaboradores.descargar.assert_not_called()


def test_reutiliza_gestor_cacheado_sin_cerrarlo(documento, config, colaboradores, monkeypatch):
    cerrar = AsyncMock()
    colaboradores.gestor.proveedores = (SimpleNamespace(aclose=cerrar),)
    obtener = MagicMock(return_value=colaboradores.gestor)
    monkeypatch.setattr(sincronizador, "obtener_gestor_embeddings", obtener)
    asyncio.run(sincronizador.run_full_sync([documento], configuracion=config))
    obtener.assert_called_once_with()
    cerrar.assert_not_awaited()
    asyncio.run(
        sincronizador.run_full_sync([documento], gestor=colaboradores.gestor, configuracion=config)
    )
    obtener.assert_called_once_with()
    cerrar.assert_not_awaited()


def test_sync_document_chunks_delega_una_unica_operacion(documento, monkeypatch):
    fragmentos = [FragmentoParaIndexar(contenido="Texto", vector=vector(), orden=1)]
    resumen = ResumenIndexacion(documento_id=8, fragmentos_indexados=1, indexado_en=FECHA)
    rpc = MagicMock(return_value=resumen)
    monkeypatch.setattr(sincronizador, "reemplazar_fragmentos_documento", rpc)
    assert sincronizador.sync_document_chunks(documento, fragmentos) == resumen
    rpc.assert_called_once_with(documento, fragmentos)


@pytest.mark.parametrize(
    ("nombre", "tipo"),
    [
        ("Preguntas_frecuentes.pdf", "application/pdf"),
        (
            "Preguntas_frecuentes.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
        ("Preguntas_frecuentes", "application/vnd.google-apps.document"),
    ],
)
def test_cli_resuelve_nombre_base_segun_formato(nombre, tipo):
    documentos = probar_rag.seleccionar_documentos(
        [{"id": "uno", "name": nombre, "mimeType": tipo}], nombres=["Preguntas_frecuentes"]
    )
    assert documentos[0].drive_id == "uno"
    assert documentos[0].tipo_mime == tipo


def test_cli_nombre_ambiguo_exige_id_y_deduplica_seleccion():
    archivos = [
        {"id": "uno", "name": "Preguntas.pdf", "mimeType": "application/pdf"},
        {"id": "dos", "name": "Preguntas.docx", "mimeType": "application/pdf"},
    ]
    with pytest.raises(probar_rag.ErrorSeleccion, match="--drive-id"):
        probar_rag.seleccionar_documentos(archivos, nombres=["Preguntas"])
    documentos = probar_rag.seleccionar_documentos(
        archivos, nombres=["Preguntas.pdf"], drive_ids=["uno"]
    )
    assert len(documentos) == 1


def test_cli_poc_no_pregunta_si_una_indexacion_falla_y_cierra_clientes(
    documento, config, monkeypatch
):
    from app import settings
    from app.providers.embeddings import dependencias
    from app.services import recuperador

    cerrar = AsyncMock()
    gestor = SimpleNamespace(proveedores=(SimpleNamespace(aclose=cerrar),))
    monkeypatch.setattr(settings, "obtener_configuracion", lambda: config)
    monkeypatch.setattr(dependencias, "obtener_gestor_embeddings", lambda: gestor)
    otro = documento.model_copy(update={"drive_id": "dos"})
    monkeypatch.setattr(
        probar_rag, "_resolver_seleccion", AsyncMock(return_value=[documento, otro])
    )
    fallo = ResultadoSincronizacion(
        resultados=[
            ResultadoIndexacionDocumento(
                drive_id=documento.drive_id, titulo=documento.titulo, error="extraccion_fallida"
            )
        ]
    )
    monkeypatch.setattr(sincronizador, "run_full_sync", AsyncMock(return_value=fallo))
    recuperar = AsyncMock()
    monkeypatch.setattr(recuperador, "recuperar_fragmentos", recuperar)
    argumentos = probar_rag.crear_analizador().parse_args(["poc", "--consulta", "Hola"])
    assert asyncio.run(probar_rag._ejecutar(argumentos)) == 1
    recuperar.assert_not_awaited()
    cerrar.assert_awaited_once()


def test_cli_poc_limita_recuperacion_a_ids_del_corpus_indexado(documento, config, monkeypatch):
    from app import settings
    from app.providers.embeddings import dependencias
    from app.services import recuperador

    gestor = SimpleNamespace(proveedores=())
    monkeypatch.setattr(settings, "obtener_configuracion", lambda: config)
    monkeypatch.setattr(dependencias, "obtener_gestor_embeddings", lambda: gestor)
    otro = documento.model_copy(update={"drive_id": "dos", "titulo": "Otro.pdf"})
    monkeypatch.setattr(
        probar_rag, "_resolver_seleccion", AsyncMock(return_value=[documento, otro])
    )
    indexacion = ResultadoSincronizacion(
        resultados=[
            ResultadoIndexacionDocumento(
                drive_id=actual.drive_id,
                titulo=actual.titulo,
                resumen=ResumenIndexacion(
                    documento_id=documento_id, fragmentos_indexados=2, indexado_en=FECHA
                ),
            )
            for actual, documento_id in zip([documento, otro], [7, 8], strict=True)
        ]
    )
    monkeypatch.setattr(sincronizador, "run_full_sync", AsyncMock(return_value=indexacion))
    recuperar = AsyncMock(
        return_value=ResultadoRecuperacion(
            fragmentos=[], mejor_similitud=None, confianza=0, umbral=0.7,
            evidencia_suficiente=False,
        )
    )
    monkeypatch.setattr(recuperador, "recuperar_fragmentos", recuperar)
    argumentos = probar_rag.crear_analizador().parse_args(
        ["poc", "--consulta", "Hola", "--top-k", "3", "--vigente", "todos"]
    )
    assert asyncio.run(probar_rag._ejecutar(argumentos)) == 0
    assert recuperar.await_args.kwargs["documento_ids"] == [7, 8]
    assert recuperar.await_args.kwargs["top_k"] == 3
    assert recuperar.await_args.kwargs["filtros"].vigente is None


@pytest.mark.parametrize(
    ("fecha", "esperada"),
    [
        (datetime(2026, 10, 23, 12), "2026-10-23T12:00:00+00:00"),
        (
            datetime.fromisoformat("2026-10-23T12:00:00-03:00"),
            "2026-10-23T15:00:00+00:00",
        ),
    ],
)
def test_cli_fecha_utc_no_depende_de_zona_local(fecha, esperada):
    assert probar_rag._formatear_fecha_utc(fecha) == esperada
