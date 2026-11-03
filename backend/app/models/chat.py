"""Contrato público del chat y salida verificable del generador."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from app.services.sanitizador import sanitizar_texto


class ModeloChat(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class ContextoChat(ModeloChat):
    materia_id: int | None = Field(default=None, gt=0)
    comision: str | None = Field(default=None, max_length=100)
    periodo: str | None = Field(default=None, max_length=100)
    consulta_pendiente: str | None = Field(default=None, max_length=2000)

    @field_validator("comision", "periodo", "consulta_pendiente")
    @classmethod
    def limpiar_opcionales(cls, valor: str | None) -> str | None:
        return (sanitizar_texto(valor) or None) if valor is not None else None


class SolicitudChat(ModeloChat):
    mensaje: str = Field(min_length=1, max_length=2000)
    contexto: ContextoChat = Field(default_factory=ContextoChat)

    @field_validator("mensaje")
    @classmethod
    def limpiar_mensaje(cls, valor: str) -> str:
        limpio = sanitizar_texto(valor)
        if not limpio:
            raise ValueError("El mensaje debe contener texto.")
        return limpio


class FuenteChat(ModeloChat):
    documento_id: int
    drive_id: str
    titulo: str
    enlace: HttpUrl | None = None
    fragmento_ids: list[int]


class DerivacionChat(ModeloChat):
    sector: str = Field(min_length=1)
    correo: str = Field(min_length=1, max_length=254)
    horario: str = Field(min_length=1)
    responsable: str | None = None

    @field_validator("sector", "horario")
    @classmethod
    def limpiar_requeridos(cls, valor: str) -> str:
        limpio = sanitizar_texto(valor)
        if not limpio:
            raise ValueError("El sector y el horario deben contener texto.")
        return limpio

    @field_validator("correo")
    @classmethod
    def validar_correo(cls, valor: str) -> str:
        correo = valor.strip()
        patron = (
            r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
            r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}"
        )
        local = correo.split("@", 1)[0]
        if (not re.fullmatch(patron, correo) or len(local) > 64
                or local.startswith(".") or local.endswith(".") or ".." in local):
            raise ValueError("El correo del sector no tiene un formato válido.")
        return correo

    @field_validator("responsable")
    @classmethod
    def limpiar_responsable(cls, valor: str | None) -> str | None:
        return (sanitizar_texto(valor) or None) if valor is not None else None


class RespuestaChat(ModeloChat):
    estado: Literal["respondida", "requiere_contexto", "sin_evidencia", "bloqueada", "derivada"]
    respuesta: str
    fuentes: list[FuenteChat] = Field(default_factory=list)
    confianza: float = Field(default=0.0, ge=0, le=1, allow_inf_nan=False)
    umbral: float = Field(ge=0, le=1, allow_inf_nan=False)
    advertencia_temporal: str | None = None
    contexto: ContextoChat
    campo_requerido: Literal["materia_id", "comision"] | None = None
    derivacion: DerivacionChat | None = None

    @model_validator(mode="after")
    def validar_derivacion(self):
        if (self.estado == "derivada") != (self.derivacion is not None):
            raise ValueError("Solo una respuesta derivada debe incluir un sector válido.")
        return self


class CitaTextual(ModeloChat):
    fragmento_id: int = Field(gt=0)
    documento_id: int | None = Field(default=None, gt=0)
    texto: str = Field(min_length=1, max_length=2000)


class SalidaGenerador(ModeloChat):
    respuesta: str = Field(min_length=1, max_length=8000)
    fragmento_ids: list[int] = Field(min_length=1, max_length=50)
    citas_textuales: list[CitaTextual] = Field(default_factory=list, max_length=50)


class MateriaChat(ModeloChat):
    id: int = Field(gt=0)
    codigo: str
    nombre: str
