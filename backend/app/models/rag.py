"""Contratos de indexación y recuperación; no crean clientes ni leen configuración."""

import math
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.settings import DIMENSION_EMBEDDINGS_RAG


def validar_vector_rag(vector: Sequence[float]) -> None:
    """Valida el valor persistible sin transformarlo ni incluirlo en errores."""
    if not isinstance(vector, (list, tuple)) or len(vector) != DIMENSION_EMBEDDINGS_RAG:
        raise ValueError("El vector no tiene la dimensión requerida por T12.")
    if any(
        isinstance(valor, bool)
        or not isinstance(valor, (int, float))
        or not math.isfinite(valor)
        for valor in vector
    ):
        raise ValueError("El vector contiene componentes inválidos.")
    norma = math.hypot(*vector)
    if not math.isfinite(norma) or norma == 0:
        raise ValueError("El vector debe tener norma finita y positiva.")


class ModeloRag(BaseModel):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True, extra="forbid")


class DocumentoDrive(ModeloRag):
    drive_id: str = Field(min_length=1)
    titulo: str = Field(min_length=1)
    tipo_mime: str = Field(min_length=1)
    enlace: str | None = None
    drive_modified_time: datetime | None = None
    drive_md5_checksum: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{32}$")

    @field_validator("drive_modified_time")
    @classmethod
    def _fecha_con_zona(cls, valor: datetime | None) -> datetime | None:
        if valor is not None and valor.utcoffset() is None:
            raise ValueError("La versión de Drive requiere zona horaria.")
        return valor

    @field_validator("drive_id", "titulo", "tipo_mime", mode="before")
    @classmethod
    def _texto_con_contenido(cls, valor: object) -> object:
        if isinstance(valor, str):
            return valor.strip()
        return valor


class DocumentoPersistido(ModeloRag):
    id: int
    titulo: str
    drive_id: str
    materia_id: int | None = None
    comision: str | None = None
    periodo: str | None = None
    vigente: bool | None = None
    estado: str | None = None
    indexado_en: datetime | None = None
    drive_modified_time: datetime | None = None
    drive_md5_checksum: str | None = None


class MetadataDocumental(ModeloRag):
    """Metadata resuelta contra el catálogo; nombre y tipo son transitorios."""

    materia_id: int | None = Field(default=None, gt=0, strict=True)
    materia_normalizada: str | None = None
    materia_nombre: str | None = None
    comision: str | None = None
    periodo: str | None = None
    tipo: str


class FragmentoParaIndexar(ModeloRag):
    contenido: str = Field(min_length=1)
    vector: list[Annotated[float, Field(strict=True, allow_inf_nan=False)]] = Field(
        min_length=DIMENSION_EMBEDDINGS_RAG,
        max_length=DIMENSION_EMBEDDINGS_RAG,
        repr=False,
    )
    orden: int = Field(gt=0, strict=True)

    @field_validator("contenido")
    @classmethod
    def _contenido_no_vacio(cls, valor: str) -> str:
        if not valor.strip():
            raise ValueError("El fragmento debe tener contenido.")
        return valor

    @field_validator("vector")
    @classmethod
    def _norma_positiva(cls, valor: list[float]) -> list[float]:
        norma = math.hypot(*valor)
        if not math.isfinite(norma) or norma == 0:
            raise ValueError("El vector debe tener norma finita y positiva.")
        return valor


class FiltrosRecuperacion(ModeloRag):
    materia_id: int | None = Field(default=None, gt=0, strict=True)
    comision: str | None = None
    periodo: str | None = None
    vigente: bool | None = True

    @field_validator("comision", "periodo")
    @classmethod
    def _normalizar_texto(cls, valor: str | None) -> str | None:
        return (valor.strip() or None) if valor is not None else None


class FragmentoRecuperado(ModeloRag):
    fragmento_id: int
    documento_id: int
    contenido: str
    orden: int | None
    similitud: float = Field(allow_inf_nan=False)
    titulo: str
    drive_id: str
    materia_id: int | None
    comision: str | None
    periodo: str | None
    vigente: bool | None


class ResumenIndexacion(ModeloRag):
    documento_id: int
    fragmentos_indexados: int = Field(gt=0)
    indexado_en: datetime


class ResultadoIndexacionDocumento(ModeloRag):
    drive_id: str
    titulo: str
    resumen: ResumenIndexacion | None = None
    error: str | None = None


class ResultadoSincronizacion(ModeloRag):
    resultados: list[ResultadoIndexacionDocumento]

    @property
    def procesados(self) -> int:
        return sum(resultado.resumen is not None for resultado in self.resultados)

    @property
    def errores(self) -> int:
        return sum(resultado.error is not None for resultado in self.resultados)


class ResultadoRecuperacion(ModeloRag):
    fragmentos: list[FragmentoRecuperado]
    mejor_similitud: float | None = Field(allow_inf_nan=False)
    confianza: float = Field(ge=0, le=1, allow_inf_nan=False)
    umbral: float = Field(ge=0, le=1, allow_inf_nan=False)
    evidencia_suficiente: bool


class ErrorSincronizacion(ModeloRag):
    drive_id: str | None = None
    categoria: str


class ResumenSincronizacionDrive(ModeloRag):
    estado: Literal["ok", "parcial", "error"] = "ok"
    drive_encontrados: int = 0
    nuevos: int = 0
    actualizados: int = 0
    inactivados: int = 0
    reactivados: int = 0
    sin_cambios: int = 0
    errores: int = 0
    detalles: list[ErrorSincronizacion] = Field(default_factory=list)
