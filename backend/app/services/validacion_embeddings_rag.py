"""Política T12 compartida, anterior a toda escritura o búsqueda vectorial."""

from collections.abc import Sequence

from app.models.rag import validar_vector_rag as validar_valor_vector
from app.providers.embeddings.base import ResultadoEmbedding, ResultadoEmbeddings
from app.settings import DIMENSION_EMBEDDINGS_RAG, MODELO_EMBEDDINGS_RAG, Configuracion


class ErrorEmbeddingIncompatible(ValueError):
    """El resultado no pertenece al espacio vectorial utilizado por RAG."""


def validar_configuracion_rag(configuracion: Configuracion) -> None:
    if (
        configuracion.embedding_primary != "gemini"
        or configuracion.gemini_embedding_modelo != MODELO_EMBEDDINGS_RAG
        or configuracion.gemini_embedding_dimension != DIMENSION_EMBEDDINGS_RAG
    ):
        raise ErrorEmbeddingIncompatible("La configuración RAG no respeta el contrato T12.")


def validar_vector_rag(vector: Sequence[float]) -> None:
    try:
        validar_valor_vector(vector)
    except (ValueError, OverflowError):
        raise ErrorEmbeddingIncompatible("El vector no es compatible con el índice RAG.") from None


def _validar_origen(resultado: ResultadoEmbedding | ResultadoEmbeddings) -> None:
    if (
        resultado.proveedor != "gemini"
        or resultado.modelo != MODELO_EMBEDDINGS_RAG
        or isinstance(resultado.dimension, bool)
        or not isinstance(resultado.dimension, int)
        or resultado.dimension != DIMENSION_EMBEDDINGS_RAG
    ):
        raise ErrorEmbeddingIncompatible(
            "El embedding no es compatible con el índice Gemini de T12."
        )


def validar_resultado_documentos(resultado: ResultadoEmbeddings, cantidad: int) -> None:
    _validar_origen(resultado)
    if cantidad <= 0 or len(resultado.vectores) != cantidad:
        raise ErrorEmbeddingIncompatible("El lote no contiene un vector por fragmento.")
    for vector in resultado.vectores:
        validar_vector_rag(vector)


def validar_resultado_consulta(resultado: ResultadoEmbedding) -> None:
    _validar_origen(resultado)
    validar_vector_rag(resultado.vector)
