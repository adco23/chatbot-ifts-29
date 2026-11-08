"""Conexión con Supabase a partir de la configuración de la aplicación."""

import base64
import json
import logging
from functools import lru_cache

from supabase import Client, ClientOptions, create_client

from app.settings import obtener_configuracion

logger = logging.getLogger(__name__)


def _rol_indicado(clave: str) -> str | None:
    """Clasificación local; no verifica firmas ni autenticación contra Supabase."""
    if clave.startswith("sb_secret_") and len(clave) > len("sb_secret_"):
        return "service_role"
    if clave.startswith("sb_publishable_"):
        return "anon"
    if clave.count(".") != 2:
        return None
    try:
        segmento = clave.split(".")[1]
        contenido = base64.urlsafe_b64decode(segmento + "=" * (-len(segmento) % 4))
        datos = json.loads(contenido)
        rol = datos.get("role") if isinstance(datos, dict) else None
        return rol if rol in {"anon", "authenticated", "service_role"} else None
    except (ValueError, UnicodeError):
        return None


@lru_cache
def obtener_cliente_supabase() -> Client | None:
    """Devuelve el cliente de Supabase, creado una sola vez por proceso.

    La clave exclusiva del backend tiene prioridad y debe indicar service_role.
    SUPABASE_KEY se conserva durante la transición: 0007 puede probarse antes de
    configurar la credencial privada y aplicar manualmente 0008.
    Sin URL o clave devuelve None; health sigue disponible sin base.
    """
    config = obtener_configuracion()
    clave_backend = config.supabase_backend_key
    seleccionada = clave_backend if clave_backend is not None else config.supabase_key
    if config.supabase_url is None or seleccionada is None:
        return None
    clave = seleccionada.get_secret_value()
    rol = _rol_indicado(clave)
    if clave_backend is not None and rol != "service_role":
        logger.error("SUPABASE_BACKEND_KEY no indica una credencial privada del backend.")
        return None
    if clave_backend is None and rol in {"anon", "authenticated"}:
        logger.warning(
            "Supabase usa una credencial pública de transición; configurar "
            "SUPABASE_BACKEND_KEY antes de aplicar 0008."
        )
    try:
        return create_client(
            config.supabase_url,
            clave,
            options=ClientOptions(
                auto_refresh_token=False, persist_session=False,
                postgrest_client_timeout=config.supabase_timeout_segundos,
            ),
        )
    except Exception as error:
        # Solo el tipo de error: el mensaje podría incluir la clave.
        logger.error("No se pudo crear el cliente de Supabase: %s", type(error).__name__)
        return None
