"""Punto de entrada de la API: crea la aplicación y registra los routers."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware  # <-- 1. Importar el middleware

from app.routers import admin, chat, health
from app.settings import obtener_configuracion


def crear_app() -> FastAPI:
    """Crea la instancia de FastAPI con sus routers."""
    config = obtener_configuracion()
    app = FastAPI(
        title="Asistente virtual · IFTS N.° 29",
        version=config.version_app,
    )

    # 2. Configurar CORS para permitir peticiones del navegador y métodos OPTIONS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],       # Permitir cualquier origen (frontend)
        allow_credentials=True,
        allow_methods=["*"],       # Permite GET, POST, OPTIONS, etc.
        allow_headers=["*"],       # Permite headers personalizados como X-Sesion-Id
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