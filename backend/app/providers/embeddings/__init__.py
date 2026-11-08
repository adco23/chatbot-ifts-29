"""Generadores de embeddings y su contrato común.

La lógica de negocio no llama a un proveedor directamente: pide un `GestorEmbeddings`
(ver `dependencias.obtener_gestor_embeddings`) y usa `embed_query` o `embed_documents`.

PENDIENTE (depende del esquema de base de datos): este paquete no lee ni escribe en la
base. Cuando existan las columnas `embedding_gemini` y `embedding_cf`, quien indexe
debe guardar cada vector en la columna de su proveedor, y quien busque debe consultar
la columna del proveedor que informa `ResultadoEmbedding.proveedor`.
"""

from app.providers.embeddings.base import (
    EmbeddingsNoDisponibles,
    ProveedorEmbeddings,
    ResultadoEmbedding,
    ResultadoEmbeddings,
    SinProveedoresDeEmbeddings,
    Vector,
    normalizar_l2,
)
from app.providers.embeddings.cloudflare import EmbeddingsCloudflare
from app.providers.embeddings.gemini import EmbeddingsGemini
from app.providers.embeddings.gestor import GestorEmbeddings

# `EmbeddingsFalso` no se exporta: es solo para pruebas y se importa desde
# app.providers.embeddings.falso.
__all__ = [
    "EmbeddingsCloudflare",
    "EmbeddingsGemini",
    "EmbeddingsNoDisponibles",
    "GestorEmbeddings",
    "ProveedorEmbeddings",
    "ResultadoEmbedding",
    "ResultadoEmbeddings",
    "SinProveedoresDeEmbeddings",
    "Vector",
    "normalizar_l2",
]
