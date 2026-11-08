"""El fallback genérico nunca mezcla otro espacio vectorial con fragmento.vector."""

from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.models.rag import FragmentoParaIndexar
from app.providers.embeddings.base import ResultadoEmbedding, ResultadoEmbeddings
from app.services.validacion_embeddings_rag import (
    ErrorEmbeddingIncompatible,
    validar_configuracion_rag,
    validar_resultado_consulta,
    validar_resultado_documentos,
)
from app.settings import DIMENSION_EMBEDDINGS_RAG, MODELO_EMBEDDINGS_RAG, Configuracion


def vector():
    return [1.0] + [0.0] * (DIMENSION_EMBEDDINGS_RAG - 1)


def resultado():
    return ResultadoEmbedding(vector(), "gemini", MODELO_EMBEDDINGS_RAG, DIMENSION_EMBEDDINGS_RAG)


def test_acepta_gemini_y_lote_completo():
    validar_configuracion_rag(Configuracion())
    validar_resultado_consulta(resultado())
    validar_resultado_documentos(
        ResultadoEmbeddings([vector(), vector()], "gemini", MODELO_EMBEDDINGS_RAG, 768), 2
    )


@pytest.mark.parametrize(
    "cambios",
    [
        {"proveedor": "cloudflare", "dimension": 1024, "vector": [1.0] * 1024},
        {"proveedor": "cloudflare"},
        {"modelo": "otro-modelo"},
        {"dimension": 1024},
        {"dimension": 768.0},
        {"vector": [1.0] * 767},
        {"vector": []},
        {"vector": [0.0] * 768},
        {"vector": [True] + [0.0] * 767},
        {"vector": ["1"] + [0.0] * 767},
        {"vector": [float("nan")] + [0.0] * 767},
        {"vector": [float("inf")] + [0.0] * 767},
    ],
)
def test_rechaza_resultado_incompatible(cambios):
    with pytest.raises(ErrorEmbeddingIncompatible):
        validar_resultado_consulta(replace(resultado(), **cambios))


@pytest.mark.parametrize("cantidad", [0, 2])
def test_rechaza_cardinalidad_documental(cantidad):
    with pytest.raises(ErrorEmbeddingIncompatible):
        validar_resultado_documentos(
            ResultadoEmbeddings([vector()], "gemini", MODELO_EMBEDDINGS_RAG, 768), cantidad
        )


def test_valida_tambien_el_ultimo_vector_del_lote():
    with pytest.raises(ErrorEmbeddingIncompatible):
        validar_resultado_documentos(
            ResultadoEmbeddings([vector(), [0.0] * 768], "gemini", MODELO_EMBEDDINGS_RAG, 768), 2
        )


@pytest.mark.parametrize(
    "cambios",
    [
        {"embedding_primary": "cloudflare"},
        {"gemini_embedding_modelo": "otro-modelo"},
        {"gemini_embedding_dimension": 1024},
    ],
)
def test_valida_configuracion_al_entrar_en_rag(cambios):
    with pytest.raises(ErrorEmbeddingIncompatible):
        validar_configuracion_rag(Configuracion(**cambios))


def test_vector_no_se_expone_en_repr_ni_se_coerciona_booleano():
    fragmento = FragmentoParaIndexar(contenido="Reglamento", vector=vector(), orden=1)
    assert "vector=" not in repr(fragmento)
    with pytest.raises(ValidationError):
        FragmentoParaIndexar(contenido="Texto", vector=[True] + [0.0] * 767, orden=1)
