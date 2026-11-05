"""T40: un evento mínimo por derivación, sin mensajes, contexto ni sesión."""

import logging

from httpx import HTTPError
from postgrest.exceptions import APIError
from supabase import Client

from app.db.conexion import obtener_cliente_supabase

logger = logging.getLogger(__name__)


def registrar_derivacion_anonima(sector: str, *, cliente: Client | None = None) -> bool:
    """Un intento de escritura; sólo los fallos remotos se convierten en False."""
    if not isinstance(sector, str) or not sector.strip() or len(sector.strip()) > 200:
        raise ValueError("Se requiere un sector de entre 1 y 200 caracteres.")
    sector_validado = sector.strip()
    base = cliente if cliente is not None else obtener_cliente_supabase()
    if base is None:
        logger.warning("Registro de derivación no disponible: cliente ausente.")
        return False

    # Construcción fuera del try: los errores de programación deben propagarse.
    operacion = base.schema("public").rpc(
        "registrar_derivacion_anonima", {"p_sector": sector_validado},
    ).retry(False)
    try:
        response = operacion.execute()
    except (APIError, HTTPError) as error:
        logger.warning("Registro de derivación falló: tipo=%s.", type(error).__name__)
        return False

    # La RPC devuelve un booleano escalar, nunca una tabla ni un string convertible.
    dato = response.data
    if dato is True:
        return True
    if dato is False:
        logger.warning("Registro de derivación sin confirmación positiva.")
        return False
    logger.warning("Registro de derivación: respuesta RPC inválida.")
    return False
