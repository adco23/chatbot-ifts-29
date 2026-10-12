"""Construcción del gestor de embeddings a partir de la configuración."""

from functools import lru_cache

from app.config import Configuracion, obtener_configuracion
from app.providers.embeddings._http import ProveedorEmbeddingsHTTP
from app.providers.embeddings.cloudflare import EmbeddingsCloudflare
from app.providers.embeddings.gemini import EmbeddingsGemini
from app.providers.embeddings.gestor import GestorEmbeddings


def crear_proveedor_embeddings(nombre: str, config: Configuracion) -> ProveedorEmbeddingsHTTP:
    """Crea un proveedor de embeddings con sus credenciales, modelo, dimensión y tiempos.

    Los nombres admitidos son los de `PROVEEDORES_EMBEDDINGS_VALIDOS` (app/config.py).
    `EmbeddingsFalso` queda afuera a propósito.

    Lanza:
        ValueError: el nombre no se conoce o al proveedor le faltan credenciales.
    """
    if nombre == "gemini":
        if config.gemini_api_key is None:
            raise ValueError("El proveedor de embeddings 'gemini' no tiene clave configurada.")
        return EmbeddingsGemini(
            api_key=config.gemini_api_key.get_secret_value(),
            modelo=config.gemini_embedding_modelo,
            dimension=config.gemini_embedding_dimension,
            timeout_segundos=config.gemini_embedding_timeout_segundos,
            timeout_documentos_segundos=config.embedding_timeout_documentos_segundos,
        )
    if nombre == "cloudflare":
        if config.cloudflare_account_id is None or config.cloudflare_api_token is None:
            raise ValueError(
                "El proveedor de embeddings 'cloudflare' necesita el identificador de la "
                "cuenta y el token de API."
            )
        return EmbeddingsCloudflare(
            api_key=config.cloudflare_api_token.get_secret_value(),
            account_id=config.cloudflare_account_id.get_secret_value(),
            modelo=config.cloudflare_embedding_modelo,
            dimension=config.cloudflare_embedding_dimension,
            timeout_segundos=config.cloudflare_embedding_timeout_segundos,
            timeout_documentos_segundos=config.embedding_timeout_documentos_segundos,
        )
    raise ValueError(f"Proveedor de embeddings desconocido: '{nombre}'.")


def crear_gestor_embeddings(config: Configuracion) -> GestorEmbeddings:
    """Crea el gestor con los proveedores que tienen credenciales, el principal primero."""
    proveedores = [
        crear_proveedor_embeddings(nombre, config)
        for nombre in config.proveedores_embeddings_disponibles
    ]
    return GestorEmbeddings(
        proveedores,
        reintentos=config.embedding_reintentos,
        backoff_base_segundos=config.embedding_backoff_base_segundos,
        backoff_maximo_segundos=config.embedding_backoff_maximo_segundos,
        presupuesto_segundos=config.embedding_presupuesto_segundos,
    )


@lru_cache
def obtener_gestor_embeddings() -> GestorEmbeddings:
    """Devuelve el gestor, construido una sola vez por proceso. Apta para `Depends`.

    Sin ninguna credencial configurada devuelve un gestor sin proveedores: la
    aplicación arranca igual y la falla aparece recién al pedir un embedding.
    """
    return crear_gestor_embeddings(obtener_configuracion())
