from fastapi import FastAPI
from app.routers import admin

# 1. Primero creamos la aplicación (definimos 'app')
app = FastAPI(title="API Chatbot IFTS 29", version="1.0.0")

# 2. Después le conectamos el router de administración
app.include_router(admin.router, prefix="/admin")

# (Acá abajo podés tener un endpoint de prueba básico si querés)
@app.get("/")
def read_root():
    return {"message": "API Backend funcionando correctamente"}