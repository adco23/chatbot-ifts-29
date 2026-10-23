"""Conexión con Supabase a partir de la configuración de la aplicación."""

import logging
from functools import lru_cache

from supabase import Client, create_client

from app.settings import obtener_configuracion

logger = logging.getLogger(__name__)


@lru_cache
def obtener_cliente_supabase() -> Client | None:
    """Devuelve el cliente de Supabase, creado una sola vez por proceso.

    Sin URL o sin clave configuradas devuelve None: la aplicación funciona sin base.
    """
    config = obtener_configuracion()
    if config.supabase_url is None or config.supabase_key is None:
        return None
    try:
        return create_client(config.supabase_url, config.supabase_key.get_secret_value())
    except Exception as error:
        # Solo el tipo de error: el mensaje podría incluir la clave.
        logger.error("No se pudo crear el cliente de Supabase: %s", type(error).__name__)
        return None
