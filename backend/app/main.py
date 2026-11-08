"""Punto de entrada de la API: crea la aplicación y registra los routers."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routers import admin, chat, health
from app.settings import obtener_configuracion


def crear_app() -> FastAPI:
    """Crea la instancia de FastAPI con sus routers."""
    config = obtener_configuracion()
    app = FastAPI(
        title="Asistente virtual · IFTS N.° 29",
        version=config.version_app,
    )

    # El widget se sirve desde otro origen y llama a POST /chat. El middleware responde
    # el preflight antes del ruteo, así que OPTIONS no consume el límite por sesión.
    # Authorization queda afuera: /admin lo llama el cron desde el servidor, no el
    # navegador. Retry-After se expone para que el widget lea la espera del 429.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origenes,
        allow_methods=["POST"],
        allow_headers=["Content-Type", "X-Sesion-Id"],
        expose_headers=["Retry-After"],
        allow_credentials=False,
        max_age=600,
    )

    app.include_router(health.router)
    app.include_router(chat.router)

    app.include_router(admin.router, prefix="/admin")

    @app.get("/")
    def read_root():
        return {"message": "API Backend funcionando correctamente"}

    return app


# Vercel y uvicorn cargan este objeto (app.main:app).
app = crear_app()
