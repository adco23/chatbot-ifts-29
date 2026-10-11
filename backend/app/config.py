"""Configuración de la aplicación a partir de variables de entorno."""

import os
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

NOMBRE_SERVICIO = "asistente-ifts29-api"


class Configuracion(BaseSettings):
    """Valores de configuración. Ninguno es obligatorio para arrancar."""

    model_config = SettingsConfigDict(env_file_encoding="utf-8", extra="ignore")

    entorno: Literal["local", "produccion"] = "local"
    version_app: str = "0.1.0"


@lru_cache
def obtener_configuracion() -> Configuracion:
    """Devuelve la configuración, leída una sola vez por proceso."""
    # El archivo .env solo se usa en local; en producción mandan las variables del entorno.
    archivo_env = None if os.getenv("ENTORNO") == "produccion" else ".env"
    return Configuracion(_env_file=archivo_env)
