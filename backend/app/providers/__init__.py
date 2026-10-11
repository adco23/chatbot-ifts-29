"""Proveedores de modelos de lenguaje y su contrato común."""

from app.providers.base import (
    ContenidoBloqueado,
    CuotaAgotada,
    ErrorNoRecuperable,
    ErrorProveedor,
    ErrorTemporal,
    ProveedorLLM,
    RespuestaLLM,
    SolicitudLLM,
    TodosLosProveedoresFallaron,
)
from app.providers.gemini import ProveedorGemini
from app.providers.gestor import GestorProveedores, SinProveedoresConfigurados
from app.providers.groq import ProveedorGroq

# `ProveedorFalso` no se exporta: es solo para pruebas y se importa desde
# app.providers.falso.
__all__ = [
    "ContenidoBloqueado",
    "CuotaAgotada",
    "ErrorNoRecuperable",
    "ErrorProveedor",
    "ErrorTemporal",
    "GestorProveedores",
    "ProveedorGemini",
    "ProveedorGroq",
    "ProveedorLLM",
    "RespuestaLLM",
    "SinProveedoresConfigurados",
    "SolicitudLLM",
    "TodosLosProveedoresFallaron",
]
