"""Configuración de la aplicación a partir de variables de entorno."""

import os
from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

NOMBRE_SERVICIO = "asistente-ifts29-api"

# Contrato T12 del único índice persistido en fragmento.vector.
MODELO_EMBEDDINGS_RAG = "gemini-embedding-001"
DIMENSION_EMBEDDINGS_RAG = 768

# Nombres válidos de proveedores de modelos de lenguaje. Coinciden con la propiedad
# `nombre` de cada implementación de `ProveedorLLM`.
PROVEEDORES_LLM_VALIDOS: tuple[str, ...] = ("gemini", "groq")

# Nombres válidos de proveedores de embeddings. Coinciden con la propiedad `nombre` de
# cada implementación de `ProveedorEmbeddings`.
PROVEEDORES_EMBEDDINGS_VALIDOS: tuple[str, ...] = ("gemini", "cloudflare")


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

    # --- Embeddings ---
    # RAG utiliza solo Gemini/768. Cloudflare sigue disponible como infraestructura de
    # contingencia, pero sus resultados no son compatibles con fragmento.vector.

    # Proveedor de embeddings que se intenta primero; el otro queda de respaldo.
    embedding_primary: str = "gemini"

    # Gemini usa para los embeddings la misma clave que para el modelo de lenguaje
    # (`gemini_api_key`). Modelo y dimensión según la documentación consultada el
    # 2026-10-11; el modelo tiene baja anunciada para el 14 de mayo de 2028.
    gemini_embedding_modelo: str = MODELO_EMBEDDINGS_RAG
    gemini_embedding_dimension: int = Field(default=DIMENSION_EMBEDDINGS_RAG, gt=0)

    # Cloudflare Workers AI por su API REST: necesita el identificador de la cuenta y
    # un token de API. Si falta alguno de los dos, el proveedor no se usa.
    cloudflare_account_id: SecretStr | None = None
    cloudflare_api_token: SecretStr | None = None
    cloudflare_embedding_modelo: str = "@cf/baai/bge-m3"
    cloudflare_embedding_dimension: int = Field(default=1024, gt=0)

    # Tiempo máximo de cada llamada para vectorizar una consulta, en segundos. Es corto
    # porque forma parte de la respuesta del chat (RNF05).
    gemini_embedding_timeout_segundos: float = Field(default=2.0, gt=0, allow_inf_nan=False)
    cloudflare_embedding_timeout_segundos: float = Field(default=2.0, gt=0, allow_inf_nan=False)

    # Tiempo máximo de cada llamada para vectorizar un lote de documentos, en segundos.
    # Es más holgado: se usa al indexar, donde no hay nadie esperando la respuesta.
    embedding_timeout_documentos_segundos: float = Field(default=10.0, gt=0, allow_inf_nan=False)

    # Reintentos por proveedor ante una falla temporal (tiempo agotado, conexión, 5xx).
    # La espera arranca en la base, se duplica en cada reintento y no supera el máximo.
    embedding_reintentos: int = Field(default=1, ge=0, le=5)
    embedding_backoff_base_segundos: float = Field(default=0.25, gt=0, allow_inf_nan=False)
    embedding_backoff_maximo_segundos: float = Field(default=1.0, gt=0, allow_inf_nan=False)

    # Presupuesto total para vectorizar una consulta, en segundos, contando reintentos y
    # respaldo. Se reparte en partes iguales entre los proveedores: con el valor por
    # defecto, cada uno dispone de 2 segundos, que es su tiempo máximo.
    # Se suma al presupuesto del modelo de lenguaje: en el camino de falla, la consulta
    # puede superar los 10 segundos de RNF05.
    embedding_presupuesto_segundos: float = Field(default=4.0, gt=0, allow_inf_nan=False)

    # La confianza es una heurística basada en el mejor coseno, no una probabilidad.
    rag_top_k: int = Field(default=5, gt=0, strict=True)
    rag_umbral_confianza: float = Field(default=0.70, ge=0, le=1, allow_inf_nan=False)

    metadata_comision_max_longitud: int = Field(default=8, ge=1, le=32)
    metadata_tipos_documentales: Annotated[list[str], NoDecode] = ["Programa", "Cronograma"]

    @field_validator("metadata_tipos_documentales", mode="before")
    @classmethod
    def _tipos_documentales(cls, valor: object) -> object:
        if isinstance(valor, str):
            valor = valor.split(",")
        if isinstance(valor, (list, tuple)):
            if not valor or any(not isinstance(v, str) or not v.strip() for v in valor):
                raise ValueError("Se requiere al menos un tipo documental no vacío.")
            return [v.strip() for v in valor]
        return valor

    @field_validator("rag_top_k", mode="before")
    @classmethod
    def _leer_top_k(cls, valor: object) -> object:
        # Las variables de entorno son texto; no aceptar decimales ni booleanos.
        if isinstance(valor, str):
            try:
                return int(valor.strip())
            except ValueError:
                raise ValueError("RAG_TOP_K debe ser un entero positivo.") from None
        return valor

    # --- Límite de solicitudes por sesión (RNF11) ---
    # Cantidad máxima de solicitudes que una sesión puede hacer dentro de la ventana, y
    # duración de la ventana en segundos. RNF11 pide limitar pero no fija los valores:
    # estos son provisorios, hasta que el equipo los defina.
    limite_solicitudes_maximo: int = Field(default=10, gt=0)
    limite_solicitudes_ventana_segundos: float = Field(default=60.0, gt=0, allow_inf_nan=False)

    # --- API de administración (RNF11) ---
    # Token compartido que exigen las rutas de administración. Sin token, o con uno
    # demasiado corto, esas rutas rechazan todas las solicitudes.
    admin_api_token: SecretStr | None = None

    # --- Base de datos ---
    # URL del proyecto de Supabase (https://<id>.supabase.co) y su clave de API. Si
    # falta alguna de las dos, la aplicación arranca sin base.
    supabase_url: str | None = None
    supabase_key: SecretStr | None = None
    # Alternativa de despliegue al archivo local; nunca registrar su contenido.
    google_service_account_json: SecretStr | None = None

    @field_validator(
        "gemini_api_key",
        "groq_api_key",
        "cloudflare_account_id",
        "cloudflare_api_token",
        "admin_api_token",
        "supabase_url",
        "supabase_key",
        "google_service_account_json",
        mode="before",
    )
    @classmethod
    def _clave_en_blanco_es_none(cls, valor: object) -> object:
        """Trata una clave vacía o de solo espacios como no configurada."""
        if isinstance(valor, str) and not valor.strip():
            return None
        return valor

    @field_validator("embedding_primary", mode="before")
    @classmethod
    def _normalizar_embedding_primary(cls, valor: object) -> object:
        """Pasa el nombre a minúsculas, le quita los espacios y comprueba que exista."""
        if not isinstance(valor, str):
            return valor
        nombre = valor.strip().lower()
        if nombre not in PROVEEDORES_EMBEDDINGS_VALIDOS:
            validos = ", ".join(PROVEEDORES_EMBEDDINGS_VALIDOS)
            raise ValueError(
                f"Proveedor desconocido en EMBEDDING_PRIMARY: '{nombre}'. "
                f"Nombres válidos: {validos}."
            )
        return nombre

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

    @model_validator(mode="after")
    def _validar_backoff_de_embeddings(self) -> Self:
        """Comprueba que la espera máxima entre reintentos no sea menor que la base."""
        if self.embedding_backoff_maximo_segundos < self.embedding_backoff_base_segundos:
            raise ValueError(
                f"EMBEDDING_BACKOFF_MAXIMO_SEGUNDOS ({self.embedding_backoff_maximo_segundos}) "
                "no puede ser menor que EMBEDDING_BACKOFF_BASE_SEGUNDOS "
                f"({self.embedding_backoff_base_segundos})."
            )
        return self

    @property
    def proveedores_disponibles(self) -> list[str]:
        """Proveedores con clave configurada, en el orden de `llm_orden_proveedores`.

        Sin ninguna clave devuelve una lista vacía.
        """
        claves = {"gemini": self.gemini_api_key, "groq": self.groq_api_key}
        return [nombre for nombre in self.llm_orden_proveedores if claves[nombre] is not None]

    @property
    def orden_embeddings(self) -> list[str]:
        """Proveedores de embeddings por prioridad: el de `embedding_primary` y el resto."""
        respaldo = [
            nombre for nombre in PROVEEDORES_EMBEDDINGS_VALIDOS if nombre != self.embedding_primary
        ]
        return [self.embedding_primary, *respaldo]

    @property
    def proveedores_embeddings_disponibles(self) -> list[str]:
        """Proveedores de embeddings con credenciales, en el orden de `orden_embeddings`.

        Sin ninguna credencial devuelve una lista vacía.
        """
        con_credenciales = {
            "gemini": self.gemini_api_key is not None,
            "cloudflare": self.cloudflare_account_id is not None
            and self.cloudflare_api_token is not None,
        }
        return [nombre for nombre in self.orden_embeddings if con_credenciales[nombre]]


@lru_cache
def obtener_configuracion() -> Configuracion:
    """Devuelve la configuración, leída una sola vez por proceso."""
    # El archivo .env solo se usa en local; en producción mandan las variables del entorno.
    archivo_env = None if os.getenv("ENTORNO") == "produccion" else ".env"
    return Configuracion(_env_file=archivo_env)
