"""Punto de entrada de la API: crea la aplicación y registra los routers."""

from fastapi import FastAPI

from app.config import obtener_configuracion
from app.routers import health, admin


def crear_app() -> FastAPI:
    """Crea la instancia de FastAPI con sus routers."""
    config = obtener_configuracion()
    app = FastAPI(
        title="Asistente virtual · IFTS N.° 29",
        version=config.version_app,
    )

    app.include_router(health.router)

    app.include_router(admin.router, prefix="/admin")

    @app.get("/")
    def read_root():
        return {"message": "API Backend funcionando correctamente"}

    return app


# Vercel y uvicorn cargan este objeto (app.main:app).
app = crear_app()
