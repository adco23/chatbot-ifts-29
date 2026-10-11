"""Endpoint de salud del servicio."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.config import NOMBRE_SERVICIO, Configuracion, obtener_configuracion

router = APIRouter(tags=["salud"])


@router.get("/health")
def salud(config: Annotated[Configuracion, Depends(obtener_configuracion)]) -> dict[str, str]:
    """Informa que el servicio está activo. No consulta servicios externos."""
    return {
        "estado": "ok",
        "servicio": NOMBRE_SERVICIO,
        "version": config.version_app,
        "entorno": config.entorno,
    }
