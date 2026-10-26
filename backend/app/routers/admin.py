"""Ejecución manual y cron sobre el mismo servicio de reconciliación."""

from fastapi import APIRouter, Depends, HTTPException, Response

from app.models.rag import ResumenSincronizacionDrive
from app.services.autenticacion_admin import verificar_token_admin
from app.services.sincronizador import SincronizacionEnCurso, sincronizar_drive_completo

router = APIRouter(dependencies=[Depends(verificar_token_admin)])


@router.post("/sync", response_model=ResumenSincronizacionDrive, tags=["Admin"])
@router.get("/sync", response_model=ResumenSincronizacionDrive, tags=["Admin"])
async def trigger_sync(response: Response) -> ResumenSincronizacionDrive:
    try:
        resultado = await sincronizar_drive_completo()
    except SincronizacionEnCurso:
        raise HTTPException(409, detail="Ya hay una sincronización en curso.") from None
    response.headers["Cache-Control"] = "no-store"
    response.status_code = {"ok": 200, "parcial": 207, "error": 503}[resultado.estado]
    return resultado
