"""Recuperación offline con gestor falso y repositorio espía."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from app.models.rag import FiltrosRecuperacion, FragmentoRecuperado
from app.providers.embeddings.base import ResultadoEmbedding
from app.services import recuperador
from app.services.validacion_embeddings_rag import ErrorEmbeddingIncompatible
from app.settings import MODELO_EMBEDDINGS_RAG, Configuracion


def embedding():
    return ResultadoEmbedding([1.0] + [0.0] * 767, "gemini", MODELO_EMBEDDINGS_RAG, 768)


def fragmento(score, identificador=1):
    return FragmentoRecuperado(
        fragmento_id=identificador, documento_id=7, contenido="Reglamento", orden=1,
        similitud=score, titulo="FAQ", drive_id="drive", materia_id=None,
        comision=None, periodo=None, vigente=True,
    )


def ejecutar(monkeypatch, filas, *, resultado=None, config=None, **kwargs):
    gestor = Mock()
    gestor.embed_query = AsyncMock(return_value=embedding() if resultado is None else resultado)
    buscar = Mock(return_value=filas)
    monkeypatch.setattr(recuperador.repositorio, "buscar_fragmentos_similares", buscar)
    obtenido = asyncio.run(recuperador.recuperar_fragmentos(
        "  ¿Cómo me inscribo?  ", gestor=gestor,
        configuracion=Configuracion() if config is None else config, **kwargs,
    ))
    return obtenido, gestor, buscar


def test_transporta_filtros_ids_y_consulta(monkeypatch):
    filtros = FiltrosRecuperacion(materia_id=2, comision="A", periodo="2026", vigente=None)
    resultado, gestor, buscar = ejecutar(
        monkeypatch, [fragmento(0.8)], filtros=filtros, documento_ids=[7], top_k=3
    )
    gestor.embed_query.assert_awaited_once_with("¿Cómo me inscribo?")
    buscar.assert_called_once_with(
        embedding().vector, filtros=filtros, top_k=3, documento_ids=[7]
    )
    assert resultado.evidencia_suficiente


@pytest.mark.parametrize("score,suficiente", [(0.699, False), (0.700, True), (0.701, True)])
def test_umbral_inclusivo_sin_descartar_fragmentos(monkeypatch, score, suficiente):
    resultado, _, _ = ejecutar(monkeypatch, [fragmento(score)])
    assert resultado.evidencia_suficiente is suficiente
    assert len(resultado.fragmentos) == 1
    assert resultado.mejor_similitud == score
    assert resultado.umbral == 0.70


def test_sin_resultados_no_hay_evidencia_aun_con_umbral_cero(monkeypatch):
    resultado, _, _ = ejecutar(monkeypatch, [], config=Configuracion(rag_umbral_confianza=0))
    assert resultado.confianza == 0
    assert resultado.mejor_similitud is None
    assert not resultado.evidencia_suficiente


def test_similitud_negativa_no_se_presenta_como_probabilidad(monkeypatch):
    resultado, _, _ = ejecutar(monkeypatch, [fragmento(-0.2)])
    assert resultado.mejor_similitud == -0.2
    assert resultado.confianza == 0
    assert resultado.fragmentos[0].similitud == -0.2


def test_orden_y_top_k_deterministas(monkeypatch):
    resultado, _, _ = ejecutar(
        monkeypatch, [fragmento(0.7, 3), fragmento(0.9, 2), fragmento(0.9, 1)], top_k=2
    )
    assert [item.fragmento_id for item in resultado.fragmentos] == [1, 2]


@pytest.mark.parametrize(
    "cambios", [
        {"proveedor": "cloudflare", "dimension": 1024, "vector": [1.0] * 1024},
        {"modelo": "otro-modelo"},
        {"dimension": 1024},
        {"vector": [0.0] * 768},
    ]
)
def test_incompatible_aborta_antes_de_busqueda(monkeypatch, cambios):
    gestor = Mock(embed_query=AsyncMock(return_value=replace(embedding(), **cambios)))
    buscar = Mock()
    monkeypatch.setattr(recuperador.repositorio, "buscar_fragmentos_similares", buscar)
    with pytest.raises(ErrorEmbeddingIncompatible):
        asyncio.run(recuperador.recuperar_fragmentos(
            "Consulta", gestor=gestor, configuracion=Configuracion()
        ))
    buscar.assert_not_called()


@pytest.mark.parametrize("texto", ["", "  ", None])
def test_consulta_vacia_no_llama_dependencias(texto):
    gestor = Mock(embed_query=AsyncMock())
    with pytest.raises(ValueError):
        asyncio.run(recuperador.recuperar_fragmentos(texto, gestor=gestor))
    gestor.embed_query.assert_not_awaited()


def test_error_de_infraestructura_no_se_convierte_en_confianza_baja(monkeypatch):
    gestor = Mock(embed_query=AsyncMock(return_value=embedding()))
    monkeypatch.setattr(
        recuperador.repositorio, "buscar_fragmentos_similares",
        Mock(side_effect=recuperador.repositorio.ErrorRepositorio("Fallo de datos")),
    )
    with pytest.raises(recuperador.repositorio.ErrorRepositorio):
        asyncio.run(recuperador.recuperar_fragmentos(
            "Consulta", gestor=gestor, configuracion=Configuracion()
        ))


def test_configuracion_incompatible_aborta_sin_embedding():
    gestor = Mock(embed_query=AsyncMock())
    with pytest.raises(ErrorEmbeddingIncompatible):
        asyncio.run(recuperador.recuperar_fragmentos(
            "Consulta", gestor=gestor, configuracion=Configuracion(embedding_primary="cloudflare")
        ))
    gestor.embed_query.assert_not_awaited()
