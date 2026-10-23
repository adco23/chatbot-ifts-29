"""Orquestación sin memoria persistida, clientes propios ni generación sin evidencia."""

import asyncio
import json
import re
from pathlib import Path

from pydantic import TypeAdapter

from app.db import repositorio
from app.drive.cliente import obtener_metadatos_archivo
from app.models.chat import (
    ContextoChat,
    FuenteChat,
    RespuestaChat,
    SalidaGenerador,
    SolicitudChat,
)
from app.models.rag import FiltrosRecuperacion
from app.providers.base import ContenidoBloqueado, SolicitudLLM
from app.services.moderador import entrada_bloqueada
from app.services.recuperador import recuperar_fragmentos
from app.services.sanitizador import normalizar_comparacion, sanitizar_texto
from app.services.temporalidad import RECORDATORIO, es_temporal, fechas_respaldadas

_PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "sistema.md"
SIN_EVIDENCIA = (
    "No cuento con evidencia suficiente para responder esa consulta. "
    "Verificá la información en el canal institucional oficial del IFTS 29."
)


class ErrorConversacion(RuntimeError):
    """Fallo técnico cuyo detalle público nunca contiene payloads."""


def _coincide(documento, filtros):
    if filtros.materia_id is not None and documento.materia_id not in (None, filtros.materia_id):
        return False
    for campo in ("comision", "periodo"):
        pedido, valor = getattr(filtros, campo), getattr(documento, campo)
        if pedido and valor is not None:
            if normalizar_comparacion(pedido) != normalizar_comparacion(valor):
                return False
    return True


def _resolver_contexto(contexto, texto, materias):
    cambios = {}
    if contexto.materia_id is None:
        consulta = normalizar_comparacion(texto)
        ids = {
            materia.id for materia in materias
            if any(re.search(r"(?<!\w)" + re.escape(normalizar_comparacion(nombre)) + r"(?!\w)",
                             consulta)
                   for nombre in (materia.nombre, materia.codigo) if nombre.strip())
        }
        if len(ids) == 1:
            cambios["materia_id"] = ids.pop()
    if contexto.comision is None:
        comision = re.search(r"\bcomision\s+([a-z]|\d{1,4}[a-z]?)(?!\w)",
                             normalizar_comparacion(texto))
        if comision:
            cambios["comision"] = comision[1].upper()
    return contexto.model_copy(update=cambios)


def validar_generacion(texto, fragmentos):
    salida = SalidaGenerador.model_validate_json(texto)
    disponibles = {fragmento.fragmento_id: fragmento for fragmento in fragmentos}
    if not salida.respuesta.strip() or any(i not in disponibles for i in salida.fragmento_ids):
        raise ValueError("Referencias inválidas.")
    usados = list(dict.fromkeys(salida.fragmento_ids))
    for cita in salida.citas_textuales:
        if cita.fragmento_id not in usados or not sanitizar_texto(cita.texto):
            raise ValueError("Cita inválida.")
        contenido = disponibles[cita.fragmento_id].contenido
        if sanitizar_texto(cita.texto) not in sanitizar_texto(contenido):
            raise ValueError("Cita sin respaldo.")
    contenidos = [disponibles[i].contenido for i in usados]
    # También controlar comillas en la prosa aunque el modelo omita declararlas.
    for cita in re.findall(r'["“«]([^"”»]+)["”»]', salida.respuesta):
        if not any(sanitizar_texto(cita) in sanitizar_texto(c) for c in contenidos):
            raise ValueError("Cita sin respaldo.")
    if not fechas_respaldadas(salida.respuesta, contenidos):
        raise ValueError("Fecha sin respaldo.")
    return salida, [disponibles[i] for i in usados]


async def resolver_fuentes(fragmentos):
    fuentes = {}
    for fragmento in fragmentos:
        clave = fragmento.documento_id
        if clave not in fuentes:
            enlace = None
            try:
                async with asyncio.timeout(2):
                    datos = await asyncio.to_thread(obtener_metadatos_archivo, fragmento.drive_id)
                if datos.get("id") == fragmento.drive_id and datos.get("webViewLink"):
                    # Se acepta exclusivamente una URL recibida de Drive, nunca del LLM.
                    tipo_enlace = FuenteChat.model_fields["enlace"].annotation
                    enlace = TypeAdapter(tipo_enlace).validate_python(datos["webViewLink"])
            except Exception:
                # Un enlace ausente no elimina una fuente con título e identidad verificables.
                enlace = None
            fuentes[clave] = FuenteChat(
                documento_id=clave, drive_id=fragmento.drive_id, titulo=fragmento.titulo,
                enlace=enlace, fragmento_ids=[],
            )
        fuentes[clave].fragmento_ids.append(fragmento.fragmento_id)
    return list(fuentes.values())


async def conversar(solicitud: SolicitudChat, *, configuracion, gestor_embeddings, gestor_llm):
    """Todas las dependencias se inyectan; no se registran textos ni excepciones remotas."""
    try:
        return await _conversar(solicitud, configuracion, gestor_embeddings, gestor_llm)
    except ContenidoBloqueado:
        return RespuestaChat(
            estado="bloqueada", respuesta="No puedo procesar esa solicitud.",
            umbral=configuracion.rag_umbral_confianza,
            contexto=solicitud.contexto.model_copy(update={"consulta_pendiente": None}),
        )
    except Exception:
        raise ErrorConversacion("No se pudo completar la consulta. Intentá nuevamente.") from None


async def _conversar(solicitud, config, embeddings, llm):
    contexto = solicitud.contexto
    pendiente = contexto.consulta_pendiente
    texto = f"{pendiente}\nAclaración: {solicitud.mensaje}" if pendiente else solicitud.mensaje
    advertencia = RECORDATORIO if es_temporal(texto) else None

    def responder(estado, respuesta, resultado=None, **extras):
        return RespuestaChat(
            estado=estado, respuesta=respuesta,
            confianza=resultado.confianza if resultado else 0.0,
            umbral=config.rag_umbral_confianza, advertencia_temporal=advertencia,
            contexto=contexto, **extras,
        )

    if entrada_bloqueada(texto):
        contexto = contexto.model_copy(update={"consulta_pendiente": None})
        return responder("bloqueada", "No puedo procesar esa solicitud.")
    materias = await asyncio.to_thread(repositorio.listar_materias_chat)
    contexto = _resolver_contexto(contexto, texto, materias)
    filtros = FiltrosRecuperacion(
        materia_id=contexto.materia_id, comision=contexto.comision, periodo=contexto.periodo,
    )
    faltantes = [campo for campo in ("materia_id", "comision") if getattr(contexto, campo) is None]

    async def recuperar(ids=None):
        return await recuperar_fragmentos(
            texto, filtros=filtros, documento_ids=ids,
            gestor=embeddings, configuracion=config,
        )

    resultado = None
    if faltantes:
        documentos = await asyncio.to_thread(repositorio.listar_documentos_chat)
        generales = [d.id for d in documentos if _coincide(d, filtros)
                     and all(getattr(d, campo) is None for campo in faltantes)]
        if generales:
            resultado = await recuperar(generales)
    if resultado is None or not resultado.evidencia_suficiente:
        resultado = await recuperar()
        if resultado.evidencia_suficiente:
            relevantes = [f for f in resultado.fragmentos if f.similitud >= resultado.umbral]
            for campo in faltantes:
                if any(getattr(f, campo) is not None for f in relevantes):
                    contexto = contexto.model_copy(update={
                        "consulta_pendiente": pendiente or solicitud.mensaje,
                    })
                    pregunta = ("¿Sobre qué materia es tu consulta?" if campo == "materia_id"
                                else "¿Sobre qué comisión es tu consulta?")
                    return responder("requiere_contexto", pregunta, resultado,
                                     campo_requerido=campo)
    contexto = contexto.model_copy(update={"consulta_pendiente": None})
    if not resultado.evidencia_suficiente:
        return responder("sin_evidencia", SIN_EVIDENCIA, resultado)
    fragmentos = [f for f in resultado.fragmentos if f.similitud >= resultado.umbral]
    solicitud_llm = SolicitudLLM(
        instrucciones_sistema=_PROMPT.read_text(encoding="utf-8"),
        contexto=json.dumps([
            {"fragmento_id": f.fragmento_id, "titulo": f.titulo, "contenido": f.contenido}
            for f in fragmentos
        ], ensure_ascii=False),
        consulta=texto, temperatura=0.1, max_tokens=1200,
    )
    generado = await llm.generar_con_respaldo(solicitud_llm)
    salida, usados = validar_generacion(generado.texto, fragmentos)
    fuentes = await resolver_fuentes(usados)
    return responder("respondida", salida.respuesta, resultado, fuentes=fuentes)
