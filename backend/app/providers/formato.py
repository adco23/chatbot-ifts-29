"""Armado del mensaje de usuario que comparten los proveedores."""

from app.providers.base import SolicitudLLM


def armar_mensaje_usuario(solicitud: SolicitudLLM) -> str:
    """Arma el texto del mensaje de usuario a partir del contexto y la consulta.

    PROVISORIO: el formato definitivo del prompt se define en T33. Hasta entonces se
    usa un bloque «Contexto» seguido de la consulta; si el contexto está vacío, solo
    la consulta.

    Las instrucciones del sistema no se incluyen acá: cada proveedor las envía en el
    campo dedicado de su API.
    """
    contexto = solicitud.contexto.strip()
    consulta = solicitud.consulta.strip()
    if not contexto:
        return consulta
    return f"Contexto:\n{contexto}\n\nConsulta:\n{consulta}"
