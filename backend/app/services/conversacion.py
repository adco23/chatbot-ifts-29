"""Orquestación sin memoria persistida, clientes propios ni generación sin evidencia."""

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import Path
from pydantic import TypeAdapter

from app.db import repositorio
from app.db import faq as repositorio_faq
from app.db import derivaciones as repositorio_derivaciones
from app.drive.cliente import obtener_metadatos_archivo
from app.models.chat import (
    ContextoChat,
    DerivacionChat,
    FuenteChat,
    RespuestaChat,
    SolicitudChat,
)
from app.models.rag import FiltrosRecuperacion, FragmentoRecuperado
from app.models.faq import FragmentoFuenteFaq
from app.providers.embeddings.base import ResultadoEmbedding
from app.providers.base import ContenidoBloqueado, SolicitudLLM
from app.services.intencion_documental import clasificar_intencion_documental, es_consulta_institucional
from app.services.cache_faq import buscar_faq
from app.services import derivacion
from app.services.validacion_embeddings_rag import (
    validar_configuracion_rag, validar_resultado_consulta,
)
from app.services.moderador import entrada_bloqueada
from app.services.postprocesador import (
    ErrorPostproceso, registrar_etapa, validar_generacion,
)
from app.services.sinonimos import normalizar_y_buscar_materia
from app.services.recuperador import recuperar_fragmentos
from app.services.sanitizador import normalizar_comparacion
from app.services.temporalidad import advertencia_temporal
from app.services.eventos_cronograma import contexto_evento, identificar_evento, respalda_evento

_PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "sistema.md"
SIN_EVIDENCIA = (
    "No cuento con evidencia suficiente para responder esa consulta. "
    "Verificá la información en el canal institucional oficial del IFTS 29."
)


class ErrorConversacion(RuntimeError):
    """Fallo técnico cuyo detalle público nunca contiene payloads."""


@dataclass
class GeneracionDocumental:
    respuesta: RespuestaChat
    fragmentos: list[FragmentoRecuperado]
    primarios: list[int] | None
    embedding: ResultadoEmbedding
    filtros: FiltrosRecuperacion
    fuentes_versionadas: list[FragmentoFuenteFaq]


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
        # 1. Intentamos resolver primero usando el módulo de sinónimos
        materia_encontrada_nombre = normalizar_y_buscar_materia(texto)
        if materia_encontrada_nombre:
            for materia in materias:
                if normalizar_comparacion(materia.nombre) == normalizar_comparacion(materia_encontrada_nombre):
                    cambios["materia_id"] = materia.id
                    break

        # 2. Si el sinónimo no matcheó, usamos la lógica original de códigos, nombres y alias automáticos
        if "materia_id" not in cambios:
            consulta = normalizar_comparacion(texto)
            ids = {
                materia.id for materia in materias
                if any(re.search(r"(?<!\w)" + re.escape(normalizar_comparacion(nombre)) + r"(?!\w)",
                                consulta)
                       for nombre in (materia.nombre, materia.codigo) if nombre.strip())
            }
            if not ids:
                for materia in materias:
                    palabras = [p for p in normalizar_comparacion(materia.nombre).split()
                                if p not in {"de", "del", "la", "las", "los", "el", "y"}]
                    alias = {"".join(p[0] for p in palabras),
                             "".join(p[0] for p in palabras[-2:])}
                    if palabras and len(palabras[0]) >= 5:
                        alias.add(palabras[0])
                    if any(len(a) >= 2 and re.search(r"(?<!\w)" + re.escape(a) + r"(?!\w)",
                                                   consulta) for a in alias):
                        ids.add(materia.id)
            if len(ids) == 1:
                cambios["materia_id"] = ids.pop()

    if contexto.comision is None:
        comision = re.search(r"\bcomision\s+([a-z]|\d{1,4}[a-z]?)(?!\w)",
                             normalizar_comparacion(texto))
        if comision:
            cambios["comision"] = comision[1].upper()
            
    if contexto.periodo is None:
        periodo = re.search(r"\b[0-9]{4}c[12]\b", normalizar_comparacion(texto))
        if periodo:
            cambios["periodo"] = periodo[0].upper()
            
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


def _texto_consulta(solicitud: SolicitudChat) -> str:
    pendiente = solicitud.contexto.consulta_pendiente
    return f"{pendiente}\nAclaración: {solicitud.mensaje}" if pendiente else solicitud.mensaje


async def conversar(solicitud: SolicitudChat, *, configuracion, gestor_embeddings, gestor_llm):
    """Todas las dependencias se inyectan; no se registran textos ni excepciones remotas."""
    try:
        respuesta = await _conversar(solicitud, configuracion, gestor_embeddings, gestor_llm)
        if respuesta.estado != "sin_evidencia":
            return respuesta
        sector = await asyncio.to_thread(
            derivacion.find_referral_department, _texto_consulta(solicitud),
        )
        if sector is None:
            return respuesta
        contacto = DerivacionChat(
            sector=sector["department"], correo=sector["email"],
            horario=sector["schedule"], responsable=sector.get("contact_person"),
        )
        correo = derivacion.generar_correo_prearmado(contacto)
        respuesta_derivada = RespuestaChat.model_validate(respuesta.model_dump() | {
            "estado": "derivada", "derivacion": contacto,
            "correo_prearmado": correo,
            "respuesta": (
                "No cuento con evidencia suficiente para responder esa consulta. "
                f"Podés consultar a {contacto.sector}."
            ),
        })
        await asyncio.to_thread(
            repositorio_derivaciones.registrar_derivacion_anonima, contacto.sector,
        )
        return respuesta_derivada
    except ContenidoBloqueado:
        return RespuestaChat(
            estado="bloqueada", respuesta="No puedo procesar esa solicitud.",
            umbral=configuracion.rag_umbral_confianza,
            contexto=solicitud.contexto.model_copy(update={"consulta_pendiente": None}),
        )
    except Exception:
        raise ErrorConversacion("No se pudo completar la consulta. Intentá nuevamente.") from None


async def generar_documental(solicitud, *, configuracion, gestor_embeddings, gestor_llm,
                             embedding_consulta=None):
    """Mismo flujo RAG para T41/T43; omite exclusivamente la consulta de caché."""
    captura = {}
    await _conversar(solicitud, configuracion, gestor_embeddings, gestor_llm,
                     usar_cache=False, captura=captura, embedding_consulta=embedding_consulta)
    return captura.get("generacion")


async def _conversar(solicitud, config, embeddings, llm, *, usar_cache=True, captura=None,
                     embedding_consulta=None):
    contexto = solicitud.contexto
    pendiente = contexto.consulta_pendiente
    texto = _texto_consulta(solicitud)
    advertencia = advertencia_temporal(texto)
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
    materias = await asyncio.to_thread(repositorio.listar_materias_chat)
    contexto = _resolver_contexto(contexto, texto, materias)
    intencion = clasificar_intencion_documental(texto, config)
    institucional = (contexto.materia_id is None and contexto.comision is None
                     and es_consulta_institucional(texto))
    if institucional:
        intencion = "general"
    advertencia = advertencia_temporal(texto, tipo_documental=intencion)
    academica = intencion in ("programa", "cronograma")
    evento = identificar_evento(texto) if intencion == "cronograma" else None
    umbral = config.umbral_rag_para_tipo(intencion) if academica else config.rag_umbral_confianza
    filtros = FiltrosRecuperacion(
        materia_id=contexto.materia_id, comision=contexto.comision, periodo=contexto.periodo,
        tipo_documental=intencion if academica else None,
    )
    faltantes = [campo for campo in ("materia_id", "comision") if getattr(contexto, campo) is None]

    async def vectorizar():
        nonlocal embedding_consulta
        validar_configuracion_rag(config)
        if embedding_consulta is None:
            embedding_consulta = await embeddings.embed_query(texto.strip())
        validar_resultado_consulta(embedding_consulta)
        return embedding_consulta

    async def recuperar(ids=None):
        return await recuperar_fragmentos(
            texto, filtros=filtros, documento_ids=ids,
            gestor=embeddings, configuracion=config,
            embedding_consulta=await vectorizar(),
        )

    resultado = None

    def pedir_contexto(campo):
        nonlocal contexto
        contexto = contexto.model_copy(update={"consulta_pendiente": pendiente or solicitud.mensaje})
        pregunta = ("¿Sobre qué materia es tu consulta?" if campo == "materia_id"
                    else "¿Sobre qué comisión es tu consulta?")
        return responder("requiere_contexto", pregunta, resultado, campo_requerido=campo)

    seleccion_programas = None
    generales = None
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
    elif faltantes:
        documentos = await asyncio.to_thread(repositorio.listar_documentos_chat)
        generales = [d.id for d in documentos if _coincide(d, filtros)
                     and (not institucional or d.tipo_documental == "general")
                     and all(getattr(d, campo) is None for campo in faltantes)]
    # Una búsqueda después de resolver también las aclaraciones T36.
    if usar_cache:
        hit = await buscar_faq(
            texto, contexto=contexto, tipo_documental=intencion if academica else "general",
            embedding=await vectorizar(), configuracion=config, resolver_fuentes=resolver_fuentes,
        )
        if hit is not None:
            return hit
    if academica:
        resultado = await recuperar(seleccion_programas)
    elif generales:
        resultado = await recuperar(generales)
    if institucional and (resultado is None or not resultado.evidencia_suficiente):
        contexto = contexto.model_copy(update={"consulta_pendiente": None})
        return responder("sin_evidencia", SIN_EVIDENCIA, resultado)
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
    if evento is not None and not any(
        f.fragmento_id in (primarios or []) and respalda_evento(f.contenido, evento)
        for f in fragmentos
    ):
        return responder("sin_evidencia", SIN_EVIDENCIA)
    instrucciones = _PROMPT.read_text(encoding="utf-8")
    if academica:
        instrucciones += (
            "\nLa respuesta académica debe fundamentarse en evidencia primaria. "
            "Citá al menos un fragmento primario. La evidencia complementaria sólo agrega "
            "contexto institucional; no demuestra horarios, contenidos ni fechas específicos. "
            "No sustituyas ni contradigas los datos primarios con información complementaria."
        )
    if evento is not None:
        instrucciones += (
            "\nRespondé sobre el evento académico solicitado usando sus bloques específicos. "
            "No equipares parcial ordinario, actividad pre-parcial y recuperatorio. "
            "Si la fuente sólo fecha el inicio de una semana, indicá la semana y cuándo "
            "empieza; no afirmes que el examen se toma ese día. No infieras año ni día exacto."
        )
    solicitud_llm = SolicitudLLM(
        instrucciones_sistema=instrucciones,
        contexto=json.dumps([
            {"fragmento_id": f.fragmento_id, "documento_id": f.documento_id,
             "titulo": f.titulo,
             "contenido": (contexto_evento(f.contenido, evento)
                           if evento is not None and f.fragmento_id in (primarios or [])
                           else f.contenido),
             "evidencia": "primaria" if primarios is None or f.fragmento_id in primarios else "complementaria"}
            for f in fragmentos
        ], ensure_ascii=False),
        consulta=texto, temperatura=0.1, max_tokens=1200,
    )
    fuentes_versionadas = []
    if captura is not None:
        # Capturar ANTES del LLM: el upsert comprobará que estas versiones siguen vigentes.
        fuentes_versionadas = await asyncio.to_thread(
            repositorio_faq.obtener_fuentes_para_guardar,
            [f.fragmento_id for f in fragmentos],
        )
        actuales = {f.fragmento_id: f for f in fuentes_versionadas}
        for fragmento in fragmentos:
            actual = actuales[fragmento.fragmento_id]
            excluidos = {"similitud"}
            if not academica and fragmento.tipo_documental is None:
                # match_fragmentos no devuelve tipo_documental. El snapshot conserva
                # su valor real; su ausencia en la RPC no representa un cambio.
                excluidos.add("tipo_documental")
            if (actual.estado_documento != "activo" or actual.vigente is not True
                    or actual.como_fragmento().model_dump(exclude=excluidos)
                    != fragmento.model_dump(exclude=excluidos)):
                raise ErrorConversacion("La evidencia cambió durante la generación FAQ.")
    generado = await llm.generar_con_respaldo(solicitud_llm)
    etapa = "validacion_generacion"
    try:
        salida, usados = validar_generacion(
            generado.texto, fragmentos, proveedor=generado.proveedor,
            fragmento_ids_primarios=primarios if academica else None,
            documento_ids_permitidos={f.documento_id for f in fragmentos},
            consulta=texto if intencion == "cronograma" else None,
        )
        etapa = "fuentes_construccion_error"
        fuentes = await resolver_fuentes(usados)
        registrar_etapa("fuentes_construidas", proveedor=generado.proveedor, cantidad=len(fuentes))
        etapa = "respuesta_final_error"
        respuesta = responder("respondida", salida.respuesta, resultado, fuentes=fuentes)
        if captura is not None:
            captura["generacion"] = GeneracionDocumental(
                respuesta, usados, primarios if academica else None,
                await vectorizar(), filtros,
                [f for f in fuentes_versionadas if f.fragmento_id in salida.fragmento_ids],
            )
        registrar_etapa("respuesta_final_validada", proveedor=generado.proveedor)
        return respuesta
    except ErrorPostproceso:
        raise
    except Exception as error:
        registrar_etapa(etapa, proveedor=generado.proveedor, tipo_error=type(error).__name__)
        raise ErrorPostproceso(etapa, "fallo_de_postproceso") from None
