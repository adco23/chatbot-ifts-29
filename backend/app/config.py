"""Configuración de la aplicación a partir de variables de entorno."""

import os
from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

NOMBRE_SERVICIO = "asistente-ifts29-api"

# Nombres válidos de proveedores de modelos de lenguaje. Coinciden con la propiedad
# `nombre` de cada implementación de `ProveedorLLM`.
PROVEEDORES_LLM_VALIDOS: tuple[str, ...] = ("gemini", "groq")


class Configuracion(BaseSettings):
    """Valores de configuración. Ninguno es obligatorio para arrancar."""

    # hide_input_in_errors evita que un error de validación muestre los valores leídos
    # del entorno, entre los que pueden estar las claves.
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
    )

    entorno: Literal["local", "produccion"] = "local"
    version_app: str = "0.1.0"

    # Claves de los proveedores. Sin clave, el proveedor queda fuera de
    # `proveedores_disponibles`; la aplicación arranca igual.
    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None

    # Modelos por defecto: disponibles sin costo según la documentación de cada
    # proveedor, consultada el 2026-10-11.
    # Gemini: modelo estable con nivel gratuito en la página de precios.
    gemini_modelo: str = "gemini-3.8-flash"
    # Groq: modelo de producción, sin fecha de baja anunciada.
    groq_modelo: str = "openai/gpt-oss-120b"

    # Tiempos máximos por proveedor, en segundos. La meta es responder el 90 % de las
    # consultas en menos de 10 segundos (RNF05): si el principal agota su tiempo, al
    # respaldo todavía le queda el suyo completo.
    gemini_timeout_segundos: float = Field(default=6.0, gt=0, allow_inf_nan=False)
    groq_timeout_segundos: float = Field(default=3.0, gt=0, allow_inf_nan=False)

    # Presupuesto total de una generación, en segundos, contando todos los intentos.
    # Relación con los tiempos por proveedor:
    # - no puede ser menor que el mayor de ellos (se valida): si lo fuera, ese
    #   proveedor nunca podría usar su tiempo completo;
    # - el valor por defecto es la suma de ambos (6 + 3 = 9), lo que deja 1 segundo
    #   de los 10 de RNF05 para el resto del circuito (caché, recuperación, registro).
    llm_presupuesto_segundos: float = Field(default=9.0, gt=0, allow_inf_nan=False)

    # Orden de prioridad de los proveedores. En el entorno se escribe como una lista
    # separada por comas; NoDecode evita que pydantic-settings la espere en JSON.
    llm_orden_proveedores: Annotated[list[str], NoDecode] = ["gemini", "groq"]

    @field_validator("gemini_api_key", "groq_api_key", mode="before")
    @classmethod
    def _clave_en_blanco_es_none(cls, valor: object) -> object:
        """Trata una clave vacía o de solo espacios como no configurada."""
        if isinstance(valor, str) and not valor.strip():
            return None
        return valor

    @field_validator("llm_orden_proveedores", mode="before")
    @classmethod
    def _normalizar_orden_proveedores(cls, valor: object) -> object:
        """Convierte la lista separada por comas y la normaliza.

        Pasa a minúsculas, quita espacios y elimina duplicados respetando el orden.
        """
        if isinstance(valor, str):
            valor = valor.split(",")
        if not isinstance(valor, list | tuple):
            return valor

        validos = ", ".join(PROVEEDORES_LLM_VALIDOS)
        nombres: list[str] = []
        for elemento in valor:
            nombre = str(elemento).strip().lower()
            if not nombre:
                continue
            if nombre not in PROVEEDORES_LLM_VALIDOS:
                raise ValueError(
                    f"Proveedor desconocido en LLM_ORDEN_PROVEEDORES: '{nombre}'. "
                    f"Nombres válidos: {validos}."
                )
            if nombre not in nombres:
                nombres.append(nombre)
        if not nombres:
            raise ValueError(
                "LLM_ORDEN_PROVEEDORES debe incluir al menos un proveedor. "
                f"Nombres válidos: {validos}."
            )
        return nombres

    @model_validator(mode="after")
    def _validar_presupuesto(self) -> Self:
        """Comprueba que el presupuesto total alcance para el mayor tiempo máximo."""
        mayor_timeout = max(self.gemini_timeout_segundos, self.groq_timeout_segundos)
        if self.llm_presupuesto_segundos < mayor_timeout:
            raise ValueError(
                f"LLM_PRESUPUESTO_SEGUNDOS ({self.llm_presupuesto_segundos}) no puede ser "
                f"menor que el mayor tiempo máximo por proveedor ({mayor_timeout})."
            )
        return self

    @property
    def proveedores_disponibles(self) -> list[str]:
        """Proveedores con clave configurada, en el orden de `llm_orden_proveedores`.

        Sin ninguna clave devuelve una lista vacía.
        """
        claves = {"gemini": self.gemini_api_key, "groq": self.groq_api_key}
        return [nombre for nombre in self.llm_orden_proveedores if claves[nombre] is not None]


@lru_cache
def obtener_configuracion() -> Configuracion:
    """Devuelve la configuración, leída una sola vez por proceso."""
    # El archivo .env solo se usa en local; en producción mandan las variables del entorno.
    archivo_env = None if os.getenv("ENTORNO") == "produccion" else ".env"
    return Configuracion(_env_file=archivo_env)
