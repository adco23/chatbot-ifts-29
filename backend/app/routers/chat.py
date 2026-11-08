"""Endpoint de conversación con límites y dependencias reemplazables en pruebas."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.models.chat import RespuestaChat, SolicitudChat
from app.providers.dependencias import obtener_gestor_proveedores
from app.providers.embeddings.dependencias import obtener_gestor_embeddings
from app.providers.embeddings.gestor import GestorEmbeddings
from app.providers.gestor import GestorProveedores
from app.services.conversacion import ErrorConversacion, conversar
from app.services.limite_solicitudes import verificar_limite_solicitudes
from app.settings import Configuracion, obtener_configuracion

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=RespuestaChat)
async def chat(
    solicitud: SolicitudChat,
    sesion: Annotated[str, Depends(verificar_limite_solicitudes)],
    configuracion: Annotated[Configuracion, Depends(obtener_configuracion)],
    embeddings: Annotated[GestorEmbeddings, Depends(obtener_gestor_embeddings)],
    llm: Annotated[GestorProveedores, Depends(obtener_gestor_proveedores)],
) -> RespuestaChat:
    try:
        return await conversar(
            solicitud, configuracion=configuracion, gestor_embeddings=embeddings, gestor_llm=llm,
        )
    except ErrorConversacion:
        raise HTTPException(503, detail={
            "codigo": "servicio_no_disponible",
            "mensaje": "No se pudo completar la consulta. Intentá nuevamente.",
        }) from None
