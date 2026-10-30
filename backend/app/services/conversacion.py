"""Orquestación sin memoria persistida, clientes propios ni generación sin evidencia."""

import asyncio
import json
import re
from pathlib import Path
from app.services.cache_faq import procesar_cache_faq

from pydantic import TypeAdapter

from app.db import repositorio
from app.drive.cliente import obtener_metadatos_archivo
from app.models.chat import (
    ContextoChat,
    FuenteChat,
    RespuestaChat,
    SolicitudChat,
)
from app.models.rag import FiltrosRecuperacion
from app.providers.base import ContenidoBloqueado, SolicitudLLM
from app.services.intencion_documental import clasificar_intencion_documental
from app.services.moderador import entrada_bloqueada
from app.services.postprocesador import (
    ErrorPostproceso, registrar_etapa, validar_generacion,
)
from app.services.recuperador import recuperar_fragmentos
from app.services.sanitizador import normalizar_comparacion
from app.services.temporalidad import RECORDATORIO, es_temporal

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
            except Exception as error:
                # Un enlace ausente no elimina una fuente con título e identidad verificables.
                registrar_etapa("fuente_enlace_no_disponible", documento_id=clave,
                                tipo_error=type(error).__name__)
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
    umbral = config.rag_umbral_confianza

    def responder(estado, respuesta, resultado=None, **extras):
        return RespuestaChat(
            estado=estado, respuesta=respuesta,
            confianza=resultado.confianza if resultado else 0.0,
            umbral=resultado.umbral if resultado else umbral, advertencia_temporal=advertencia,
            contexto=contexto, **extras,
        )

    if entrada_bloqueada(texto):
        contexto = contexto.model_copy(update={"consulta_pendiente": None})
        return responder("bloqueada", "No puedo procesar esa solicitud.")

    # ----------------------------------------------------
    # NUEVO: Intercepción por Caché de Preguntas Frecuentes (Bloque 6)
    # ----------------------------------------------------
    respuesta_cache = await procesar_cache_faq(
        mensaje=texto,
        contexto=contexto.model_dump() if hasattr(contexto, "model_dump") else dict(contexto),
        gestor_embeddings=embeddings,
        configuracion=config,
    )
    if respuesta_cache:
        # Si hay acierto en caché, limpiamos pendiente y devolvemos la respuesta de inmediato
        contexto_actualizado = contexto.model_copy(update={"consulta_pendiente": None})
        return RespuestaChat(
            estado=respuesta_cache["estado"],
            respuesta=respuesta_cache["respuesta"],
            fuentes=respuesta_cache.get("fuentes", []),
            confianza=respuesta_cache.get("confianza", 1.0),
            umbral=respuesta_cache.get("umbral", umbral),
            advertencia_temporal=advertencia,
            contexto=contexto_actualizado,
        )
    # ----------------------------------------------------

    materias = await asyncio.to_thread(repositorio.listar_materias_chat)
    contexto = _resolver_contexto(contexto, texto, materias)
    intencion = clasificar_intencion_documental(texto, config)
    academica = intencion in ("programa", "cronograma")
    umbral = config.umbral_rag_para_tipo(intencion) if academica else config.rag_umbral_confianza
    filtros = FiltrosRecuperacion(
        materia_id=contexto.materia_id, comision=contexto.comision, periodo=contexto.periodo,
        tipo_documental=intencion if academica else None,
    )
    faltantes = [campo for campo in ("materia_id", "comision") if getattr(contexto, campo) is None]

    async def recuperar(ids=None):
        return await recuperar_fragmentos(
            texto, filtros=filtros, documento_ids=ids,
            gestor=embeddings, configuracion=config,
        )

    resultado = None

    def pedir_contexto(campo):
        nonlocal contexto
        contexto = contexto.model_copy(update={"consulta_pendiente": pendiente or solicitud.mensaje})
        pregunta = ("¿Sobre qué materia es tu consulta?" if campo == "materia_id"
                    else "¿Sobre qué comisión es tu consulta?")
        return responder("requiere_contexto", pregunta, resultado, campo_requerido=campo)

    if academica:
        documentos = await asyncio.to_thread(repositorio.listar_documentos_chat)
        cobertura = [d for d in documentos if d.tipo_documental == intencion
                     and d.estado == "activo" and d.vigente is True
                     and (contexto.materia_id is None or d.materia_id == contexto.materia_id)
                     and (contexto.periodo is None or d.periodo is not None
                          and normalizar_comparacion(d.periodo) == normalizar_comparacion(contexto.periodo))]
        if contexto.materia_id is None:
            if any(d.materia_id is not None for d in cobertura):
                return pedir_contexto("materia_id")
            contexto = contexto.model_copy(update={"consulta_pendiente": None})
            return responder("sin_evidencia", SIN_EVIDENCIA)
        especificos = any(d.comision is not None for d in cobertura)
        comunes = any(d.comision is None for d in cobertura)
        if contexto.comision is None and especificos and intencion == "cronograma":
            return pedir_contexto("comision")
        seleccion_programas = None
        if intencion == "programa" and contexto.comision is None and especificos and not comunes:
            comisiones = {d.comision.strip().upper() for d in cobertura if d.comision is not None}
            if len(comisiones) > 1:
                firmas = await asyncio.to_thread(
                    repositorio.obtener_firmas_contenido_documentos, [d.id for d in cobertura],
                )
                if len(firmas) != len(cobertura) or len(set(firmas.values())) != 1:
                    return pedir_contexto("comision")
            # La comisión procede de un documento existente, no se asigna al contexto.
            # Sólo se usa para la búsqueda estricta, acotada a los documentos aplicables.
            representante = min(cobertura, key=lambda d: d.id)
            filtros = filtros.model_copy(update={"comision": representante.comision})
            seleccion_programas = [d.id for d in cobertura if d.comision == representante.comision]
        resultado = await recuperar(seleccion_programas)
    elif faltantes:
        documentos = await asyncio.to_thread(repositorio.listar_documentos_chat)
        generales = [d.id for d in documentos if _coincide(d, filtros)
                     and all(getattr(d, campo) is None for campo in faltantes)]
        if generales:
            resultado = await recuperar(generales)
    if not academica and (resultado is None or not resultado.evidencia_suficiente):
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
    primarios = resultado.fragmento_ids_primarios
    if academica and (not primarios or not any(f.fragmento_id in primarios for f in fragmentos)):
        return responder("sin_evidencia", SIN_EVIDENCIA)
    instrucciones = _PROMPT.read_text(encoding="utf-8")
    if academica:
        instrucciones += (
            "\nLa respuesta académica debe fundamentarse en evidencia primaria. "
            "Citá al menos un fragmento primario. La evidencia complementaria sólo agrega "
            "contexto institucional; no demuestra horarios, contenidos ni fechas específicos. "
            "No sustituyas ni contradigas los datos primarios con información complementaria."
        )
    solicitud_llm = SolicitudLLM(
        instrucciones_sistema=instrucciones,
        contexto=json.dumps([
            {"fragmento_id": f.fragmento_id, "documento_id": f.documento_id,
             "titulo": f.titulo, "contenido": f.contenido,
             "evidencia": "primaria" if primarios is None or f.fragmento_id in primarios else "complementaria"}
            for f in fragmentos
        ], ensure_ascii=False),
        consulta=texto, temperatura=0.1, max_tokens=1200,
    )
    generado = await llm.generar_con_respaldo(solicitud_llm)
    etapa = "validacion_generacion"
    try:
        salida, usados = validar_generacion(
            generado.texto, fragmentos, proveedor=generado.proveedor,
            fragmento_ids_primarios=primarios if academica else None,
            documento_ids_permitidos={f.documento_id for f in fragmentos},
        )
        
        etapa = "fuentes_construccion_error"
        fuentes = await resolver_fuentes(usados)
        registrar_etapa("fuentes_construidas", proveedor=generado.proveedor, cantidad=len(fuentes))

        # ----------------------------------------------------
        # NUEVO: Auto-aprendizaje de Caché FAQ (Guardado automático)
        # ----------------------------------------------------
        try:
            res_emb = await embeddings.embed_query(texto.strip())
            vector_nuevo = getattr(res_emb, "vector", None)
            if vector_nuevo and len(vector_nuevo) == 768:
                await asyncio.to_thread(
                    repositorio.guardar_pregunta_frecuente,
                    pregunta=texto,
                    respuesta=salida.respuesta,
                    vector_embedding=vector_nuevo,
                    materia_id=contexto.materia_id,
                    comision=contexto.comision,
                )
        except Exception as cache_err:
            # Un fallo en el autoguardado nunca debe interrumpir la respuesta al usuario
            print(f"=== Error al auto-guardar en caché FAQ: {cache_err} ===")
        # ----------------------------------------------------

        etapa = "respuesta_final_error"
        respuesta = responder("respondida", salida.respuesta, resultado, fuentes=fuentes)
        registrar_etapa("respuesta_final_validada", proveedor=generado.proveedor)
        return respuesta
    except ErrorPostproceso:
        raise
    except Exception as error:
        registrar_etapa(etapa, proveedor=generado.proveedor, tipo_error=type(error).__name__)
        raise ErrorPostproceso(etapa, "fallo_de_postproceso") from None