import os
from fastapi import APIRouter, Header, HTTPException
from app.services.sincronizador import run_full_sync

router = APIRouter()

# Leemos el token secreto de las variables de entorno (configurado en Vercel)
# Si no existe (ej: entorno local sin .env), usa un valor por defecto.
CRON_SECRET = os.getenv("CRON_SECRET", "mi_token_secreto_local")

@router.post("/sync", tags=["Admin"])
async def trigger_sync(authorization: str = Header(None)):
    """
    T27: Endpoint protegido.
    Este endpoint será llamado diariamente por Vercel Cron.
    """
    # 1. Capa de seguridad: Verificamos que Vercel envíe el token correcto
    if authorization != f"Bearer {CRON_SECRET}":
        raise HTTPException(status_code=401, detail="No autorizado para sincronizar")
        
    # 2. Acá en el futuro tu orquestador va a llamar a drive/cliente.py.
    # Por ahora, usamos un pequeño diccionario de prueba para cerrar el flujo:
    mock_archivos_drive = {
        "/drive/docs/reglamento_2026.pdf": "hash_nuevo_123"
    }
    
    # 3. Disparamos la función que acabás de probar por consola
    resultado = run_full_sync(mock_archivos_drive)
    
    # 4. Le devolvemos el JSON a Vercel
    return resultado