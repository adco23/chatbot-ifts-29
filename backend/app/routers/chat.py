"""Endpoint de conversación con límites y dependencias reemplazables en pruebas."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException

from app.models.chat import RespuestaChat, SolicitudChat
from app.providers.dependencias import obtener_gestor_proveedores
from app.providers.embeddings.dependencias import obtener_gestor_embeddings
from app.providers.embeddings.gestor import GestorEmbeddings
from app.providers.gestor import GestorProveedores
from app.services.conversacion import ErrorConversacion, conversar
from app.services.limite_solicitudes import verificar_limite_solicitudes
from app.settings import Configuracion, obtener_configuracion
from app.services.menu import obtener_o_crear_sesion, procesar_mensaje_menu

router = APIRouter()

@router.post("/chat", response_model=RespuestaChat)
async def chat(
    solicitud: SolicitudChat,
    sesion_id: Annotated[str, Header(alias="X-Sesion-Id")],
    configuracion: Annotated[Configuracion, Depends(obtener_configuracion)],
    embeddings: Annotated[GestorEmbeddings, Depends(obtener_gestor_embeddings)],
    llm: Annotated[GestorProveedores, Depends(obtener_gestor_proveedores)],
) -> RespuestaChat:
    try:
        texto_usuario = solicitud.mensaje.strip()
        sesion = obtener_o_crear_sesion(sesion_id)
        estado_actual = sesion.get("estado", "menu_principal")

        # 1. ¿El usuario quiere salir al modo libre con el "0" estando en el menú principal?
        # O si ya está en modo libre y NO mandó un comando de menú/volver:
        if estado_actual == "modo_libre" and texto_usuario.lower() not in ["menu", "volver", "inicio", "0", "1", "2", "3", "4", "5", "6"]:
            return await conversar(
                solicitud, configuracion=configuracion, gestor_embeddings=embeddings, gestor_llm=llm,
            )

        # Si mandó "0" estando en el menú principal, activamos el modo libre:
        if estado_actual == "menu_principal" and texto_usuario == "0":
            sesion["estado"] = "modo_libre"
            return RespuestaChat(
                respuesta="🔍 **Modo de consulta libre activado.** Escribí tu pregunta directamente y la buscaré en la documentación.",
                estado="respondida",
                umbral=0.0,
                contexto=solicitud.contexto
            )

        # 2. Todo lo demás (números de menú, letras de comisiones A/B/C/D, navegación) 
        # se lo delegamos por completo al motor de menús.
        resultado_menu = procesar_mensaje_menu(user_id=sesion_id, mensaje=texto_usuario)

        return RespuestaChat(
            respuesta=resultado_menu["respuesta"],
            estado="respondida",
            umbral=0.0,
            contexto=solicitud.contexto
        )
        
    except ErrorConversacion:
        raise HTTPException(503, detail={
            "codigo": "servicio_no_disponible",
            "mensaje": "No se pudo completar la consulta. Intentá nuevamente.",
        }) from None