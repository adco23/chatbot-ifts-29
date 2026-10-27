"""Contrato público del chat y salida verificable del generador."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

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


class RespuestaChat(ModeloChat):
    estado: Literal["respondida", "requiere_contexto", "sin_evidencia", "bloqueada"]
    respuesta: str
    fuentes: list[FuenteChat] = Field(default_factory=list)
    confianza: float = Field(default=0.0, ge=0, le=1, allow_inf_nan=False)
    umbral: float = Field(ge=0, le=1, allow_inf_nan=False)
    advertencia_temporal: str | None = None
    contexto: ContextoChat
    campo_requerido: Literal["materia_id", "comision"] | None = None


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
