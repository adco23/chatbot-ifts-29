"""Construcción del gestor de proveedores a partir de la configuración."""

from functools import lru_cache

from app.settings import Configuracion, obtener_configuracion
from app.providers._http import ProveedorHTTP
from app.providers.gemini import ProveedorGemini
from app.providers.gestor import GestorProveedores
from app.providers.groq import ProveedorGroq

# Nombre de proveedor → clase que lo implementa. Las claves coinciden con
# `PROVEEDORES_LLM_VALIDOS` de app/config.py. `ProveedorFalso` queda afuera a propósito.
REGISTRO_PROVEEDORES: dict[str, type[ProveedorHTTP]] = {
    "gemini": ProveedorGemini,
    "groq": ProveedorGroq,
}


def crear_proveedor(nombre: str, config: Configuracion) -> ProveedorHTTP:
    """Crea un proveedor con su clave, modelo y tiempo máximo configurados.

    Los valores se leen de los campos `<nombre>_api_key`, `<nombre>_modelo` y
    `<nombre>_timeout_segundos` de la configuración.

    Lanza:
        ValueError: el nombre no está registrado o el proveedor no tiene clave.
    """
    clase = REGISTRO_PROVEEDORES.get(nombre)
    if clase is None:
        raise ValueError(f"Proveedor sin registrar: '{nombre}'.")
    clave = getattr(config, f"{nombre}_api_key")
    if clave is None:
        raise ValueError(f"El proveedor '{nombre}' no tiene clave configurada.")
    return clase(
        api_key=clave.get_secret_value(),
        modelo=getattr(config, f"{nombre}_modelo"),
        timeout_segundos=getattr(config, f"{nombre}_timeout_segundos"),
    )


def crear_gestor_proveedores(config: Configuracion) -> GestorProveedores:
    """Crea el gestor con los proveedores que tienen clave, en el orden configurado."""
    proveedores = [crear_proveedor(nombre, config) for nombre in config.proveedores_disponibles]
    return GestorProveedores(proveedores, presupuesto_segundos=config.llm_presupuesto_segundos)


@lru_cache
def obtener_gestor_proveedores() -> GestorProveedores:
    """Devuelve el gestor, construido una sola vez por proceso. Apta para `Depends`.

    Sin ninguna clave configurada devuelve un gestor sin proveedores: la aplicación
    arranca igual y la falla aparece recién al pedir una generación.
    """
    return crear_gestor_proveedores(obtener_configuracion())
