"""FAQ como optimización del RAG, con evidencia vigente y sin generación adicional."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime

from app.db import faq as repositorio
from app.models.chat import ContextoChat, FuenteChat, RespuestaChat
from app.models.faq import CoincidenciaFaq, ContextoFaq, FragmentoFuenteFaq
from app.models.rag import FragmentoRecuperado
from app.providers.embeddings.base import ResultadoEmbedding
from app.services.postprocesador import validar_generacion
from app.services.temporalidad import advertencia_temporal
from app.services.eventos_cronograma import identificar_evento
from app.services.comisiones import cargar_catalogo, normalizar_periodo
from app.services.objetivo_consulta import (
    contenido_pertinente, faq_compatible, identificar_objetivo, total_plan_respaldado,
)
from app.services.validacion_embeddings_rag import (
    validar_configuracion_rag,
    validar_resultado_consulta,
)
from app.settings import Configuracion

logger = logging.getLogger(__name__)


def _coincide_contexto(faq: CoincidenciaFaq, pedido: ContextoFaq) -> bool:
    if faq.tipo_documental != pedido.tipo_documental:
        return False
    if pedido.materia_id is None:
        if faq.materia_id is not None:
            return False
    elif faq.materia_id not in (None, pedido.materia_id):
        return False
    if pedido.tipo_documental in ("programa", "cronograma"):
        if pedido.materia_id is None or faq.materia_id != pedido.materia_id:
            return False
    if pedido.comision is None:
        if faq.comision is not None:
            return False
    elif faq.comision not in (None, pedido.comision):
        return False
    if pedido.tipo_documental == "cronograma" and (
        pedido.comision is None or faq.comision != pedido.comision
    ):
        return False
    if faq.periodo != pedido.periodo:
        return False
    return True


def _fuente_primaria(fuente: FragmentoFuenteFaq, pedido: ContextoFaq) -> bool:
    if not fuente.es_primaria or fuente.tipo_documental != pedido.tipo_documental:
        return False
    if fuente.materia_id != pedido.materia_id:
        return False
    if pedido.periodo is not None and fuente.periodo != pedido.periodo:
        return False
    if pedido.tipo_documental == "cronograma":
        return pedido.comision is not None and fuente.comision in (pedido.comision, "TODAS")
    return fuente.comision in (None, "TODAS", pedido.comision)


async def buscar_faq(
    texto: str, *, contexto: ContextoChat, tipo_documental: str,
    embedding: ResultadoEmbedding, configuracion: Configuracion,
    resolver_fuentes: Callable[[Sequence[FragmentoRecuperado]], Awaitable[list[FuenteChat]]],
    catalogo=None,
) -> RespuestaChat | None:
    """Una búsqueda. Un candidato inválido produce MISS seguro.

    El orquestador ya aplicó moderación y resolvió contexto. Un embedding de otro
    espacio es un error de contrato, nunca una búsqueda FAQ compatible.
    """
    validar_configuracion_rag(configuracion)
    validar_resultado_consulta(embedding)
    catalogo = catalogo or await asyncio.to_thread(cargar_catalogo)
    periodo = contexto.periodo or catalogo.periodo_activo
    objetivo = identificar_objetivo(texto)
    try:
        periodo = normalizar_periodo(periodo)
        if contexto.comision is not None and not catalogo.admite(contexto.comision, periodo):
            return None
        pedido = ContextoFaq(
            materia_id=contexto.materia_id, comision=contexto.comision,
            periodo=contexto.periodo, tipo_documental=tipo_documental,
        )
        umbral_evidencia = configuracion.umbral_rag_para_tipo(tipo_documental)
        faq = await asyncio.to_thread(
            repositorio.buscar_en_cache_faq, embedding.vector,
            contexto=contexto, tipo_documental=tipo_documental,
            umbral=configuracion.umbral_cache_faq, umbral_evidencia=umbral_evidencia,
        )
        if faq is None:
            return None
        if not faq_compatible(texto, faq.pregunta_canonica, faq.respuesta):
            return None
        if tipo_documental == "cronograma":
            solicitado = identificar_evento(texto)
            canonico = identificar_evento(faq.pregunta_canonica)
            if solicitado is not None and canonico is not None and solicitado != canonico:
                return None
        if (
            faq.estado != "activa" or not _coincide_contexto(faq, pedido)
            or faq.similitud < configuracion.umbral_cache_faq
            or faq.confianza_evidencia < umbral_evidencia
            or (faq.vigencia_hasta is not None
                and faq.vigencia_hasta < datetime.now(UTC).date())
        ):
            return None
        evidencia = await asyncio.to_thread(
            repositorio.obtener_evidencia_faq, faq.id, faq.generada_en,
        )
        if not evidencia or any(
            fuente.estado_documento != "activo" or fuente.vigente is not True
            or not catalogo.documento_habilitado(fuente, periodo)
            for fuente in evidencia
        ):
            return None
        for fuente in evidencia:
            for campo in ("materia_id", "comision", "periodo"):
                valor, requerido = getattr(fuente, campo), getattr(pedido, campo)
                if campo == "comision" and valor == "TODAS":
                    continue
                if valor is not None and valor != requerido:
                    return None
        fragmentos = [fuente.como_fragmento() for fuente in evidencia]
        originales = {f.fragmento_id: f for f in fragmentos}
        fragmentos = [f.model_copy(update={"contenido": contenido_pertinente(
            f.contenido, objetivo, f.titulo,
        )}) for f in fragmentos]
        fragmentos = [f for f in fragmentos if f.contenido.strip()]
        if not fragmentos:
            return None
        primarios = None
        if pedido.tipo_documental in ("programa", "cronograma"):
            primarios = [fuente.fragmento_id for fuente in evidencia
                         if _fuente_primaria(fuente, pedido)]
            if not primarios:
                return None
        elif pedido.materia_id is None and any(f.materia_id is not None for f in evidencia):
            return None
        if pedido.comision is None and any(f.comision is not None for f in evidencia):
            return None
        salida, usados = validar_generacion(json.dumps({
            "respuesta": faq.respuesta,
            "fragmento_ids": [fragmento.fragmento_id for fragmento in fragmentos],
            "citas_textuales": [],
        }), fragmentos, fragmento_ids_primarios=primarios,
            documento_ids_permitidos={f.documento_id for f in fragmentos},
            consulta=texto if pedido.tipo_documental == "cronograma" else None)
        if not total_plan_respaldado(texto, [f.contenido for f in usados], respuesta=salida.respuesta):
            return None
        usados = [originales[f.fragmento_id] for f in usados]
        fuentes = await resolver_fuentes(usados)
        esperados = {
            identidad: {f.fragmento_id for f in usados if f.documento_id == identidad}
            for identidad in {f.documento_id for f in usados}
        }
        if (not fuentes or len({f.documento_id for f in fuentes}) != len(fuentes)
                or {f.documento_id: set(f.fragmento_ids) for f in fuentes} != esperados):
            return None
        respuesta = RespuestaChat(
            estado="respondida", respuesta=salida.respuesta, fuentes=fuentes,
            confianza=max(0.0, min(1.0, faq.similitud)), umbral=configuracion.umbral_cache_faq,
            advertencia_temporal=advertencia_temporal(texto, tipo_documental=tipo_documental),
            contexto=contexto.model_copy(update={"consulta_pendiente": None}),
        )
    except Exception as error:
        logger.warning("faq_miss_tecnico tipo=%s", type(error).__name__)
        return None
    # La respuesta completa ya pasó validación; la métrica no condiciona su entrega.
    try:
        await asyncio.to_thread(repositorio.incrementar_acierto_cache_faq, faq.id)
    except Exception as error:
        logger.warning("faq_acierto_no_registrado id=%s tipo=%s", faq.id, type(error).__name__)
    logger.info("faq_hit id=%s", faq.id)
    return respuesta
