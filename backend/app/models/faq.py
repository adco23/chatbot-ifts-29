"""Contratos internos de FAQ: contexto, evidencia y cargas explícitas."""

import json
import re
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.rag import FragmentoRecuperado, TipoDocumental, validar_vector_rag
from app.services.sanitizador import normalizar_comparacion, sanitizar_texto


class ModeloFaq(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


def normalizar_comision(valor: str | None) -> str | None:
    if valor is None:
        return None
    valor = valor.strip().upper()
    if not valor or valor in ("GENERAL", "COM-GENERAL"):
        return None
    if valor.startswith("COM-"):
        valor = valor[4:]
    if not re.fullmatch(r"[A-Z0-9]{1,32}", valor):
        raise ValueError("Comisión FAQ inválida.")
    return valor


class ContextoFaq(ModeloFaq):
    materia_id: int | None = Field(default=None, gt=0)
    comision: str | None = None
    periodo: str | None = None
    tipo_documental: TipoDocumental = "general"

    @field_validator("comision")
    @classmethod
    def _comision(cls, valor: str | None) -> str | None:
        return normalizar_comision(valor)

    @field_validator("periodo")
    @classmethod
    def _periodo(cls, valor: str | None) -> str | None:
        if valor is None:
            return None
        valor = valor.strip().upper()
        if not valor:
            return None
        if not re.fullmatch(r"[0-9]{4}C[12]", valor):
            raise ValueError("Período FAQ inválido.")
        return valor


class CoincidenciaFaq(ContextoFaq):
    id: int = Field(gt=0)
    pregunta_canonica: str = Field(min_length=1, max_length=2000)
    respuesta: str = Field(min_length=1, max_length=8000)
    estado: Literal["activa", "pendiente", "inactiva"]
    vigencia_hasta: date | None
    similitud: float = Field(ge=-1, le=1, allow_inf_nan=False)
    confianza_evidencia: float = Field(ge=0, le=1, allow_inf_nan=False)
    generada_en: datetime

    @field_validator("generada_en", mode="before")
    @classmethod
    def _generacion(cls, valor: object) -> object:
        if isinstance(valor, str):
            valor = datetime.fromisoformat(valor)
        if isinstance(valor, datetime) and valor.utcoffset() is not None:
            raise ValueError("La generación FAQ requiere timestamp UTC sin zona.")
        return valor

    @field_validator("vigencia_hasta", mode="before")
    @classmethod
    def _fecha(cls, valor: object) -> object:
        return date.fromisoformat(valor) if isinstance(valor, str) else valor

    @field_validator("pregunta_canonica", "respuesta")
    @classmethod
    def _contenido(cls, valor: str) -> str:
        limpio = sanitizar_texto(valor)
        if not limpio:
            raise ValueError("Contenido FAQ vacío.")
        return limpio


class FuenteFaq(ModeloFaq):
    drive_id: str = Field(min_length=1)
    fragmento_ids: list[int] = Field(min_length=1, max_length=50)
    es_primaria: bool
    # Opaco: lo produce PostgreSQL y se devuelve sin reinterpretar timestamps.
    version_documento: dict[str, Any] = Field(min_length=1)

    @field_validator("fragmento_ids")
    @classmethod
    def _identidades(cls, valores: list[int]) -> list[int]:
        if any(i <= 0 for i in valores) or len(set(valores)) != len(valores):
            raise ValueError("Referencias FAQ inválidas.")
        return valores


class FragmentoFuenteFaq(ModeloFaq):
    fragmento_id: int = Field(gt=0)
    documento_id: int = Field(gt=0)
    contenido: str = Field(min_length=1)
    orden: int | None
    titulo: str = Field(min_length=1)
    drive_id: str = Field(min_length=1)
    materia_id: int | None
    comision: str | None
    periodo: str | None
    vigente: bool | None
    tipo_documental: TipoDocumental | None
    estado_documento: str | None
    version_documento: dict[str, Any] = Field(min_length=1)
    es_primaria: bool = False

    def como_fragmento(self) -> FragmentoRecuperado:
        return FragmentoRecuperado(**self.model_dump(exclude={
            "estado_documento", "version_documento", "es_primaria",
        }), similitud=0.0)


class FaqCurada(ContextoFaq):
    pregunta_canonica: str = Field(min_length=1, max_length=2000)
    pregunta_normalizada: str = Field(min_length=1, max_length=2000)
    respuesta: str = Field(min_length=1, max_length=8000)
    embedding: list[float] = Field(repr=False)
    confianza_evidencia: float = Field(ge=0, le=1, allow_inf_nan=False)
    vigencia_hasta: date | None = None
    # Presupuesto de ejecución, no es una columna ni parte de la identidad FAQ.
    fecha_limite: datetime | None = None

    @field_validator("fecha_limite")
    @classmethod
    def _limite(cls, valor: datetime | None) -> datetime | None:
        if valor is not None:
            if valor.utcoffset() is None:
                raise ValueError("El límite de regeneración requiere zona horaria.")
            return valor.astimezone(UTC)
        return None

    @field_validator("pregunta_canonica", "respuesta")
    @classmethod
    def _contenido(cls, valor: str) -> str:
        limpio = sanitizar_texto(valor)
        if not limpio:
            raise ValueError("Contenido FAQ vacío.")
        return limpio

    @field_validator("embedding", mode="before")
    @classmethod
    def _vector(cls, valor: object) -> object:
        # PostgREST representa vector como texto al leer una FAQ pendiente.
        if isinstance(valor, str):
            valor = json.loads(valor)
        validar_vector_rag(valor)
        return valor

    @field_validator("vigencia_hasta", mode="before")
    @classmethod
    def _fecha(cls, valor: object) -> object:
        return date.fromisoformat(valor) if isinstance(valor, str) else valor

    @model_validator(mode="after")
    def _normalizada(self):
        if self.pregunta_normalizada != normalizar_comparacion(self.pregunta_canonica):
            raise ValueError("La clave FAQ no corresponde a la pregunta canónica.")
        return self


class FaqPendiente(ContextoFaq):
    id: int = Field(gt=0)
    pregunta_canonica: str = Field(min_length=1, max_length=2000)
    embedding: list[float] | None = Field(default=None, repr=False)
    vigencia_hasta: date | None = None

    @field_validator("embedding", mode="before")
    @classmethod
    def _vector(cls, valor: object) -> object:
        return FaqCurada._vector(valor) if valor is not None else None

    @field_validator("vigencia_hasta", mode="before")
    @classmethod
    def _fecha(cls, valor: object) -> object:
        return date.fromisoformat(valor) if isinstance(valor, str) else valor


class ResumenCargaFaq(ModeloFaq):
    id: int = Field(gt=0)
    creada: bool
