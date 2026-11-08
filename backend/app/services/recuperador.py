"""Recuperación T31/T32: no genera respuestas ni llama a un LLM."""

import asyncio
import logging
from collections.abc import Sequence

from app.db import repositorio
from app.models.rag import FiltrosRecuperacion, ResultadoRecuperacion
from app.providers.embeddings.dependencias import obtener_gestor_embeddings
from app.providers.embeddings.gestor import GestorEmbeddings
from app.providers.embeddings.base import ResultadoEmbedding
from app.services.eventos_cronograma import consulta_dirigida, identificar_evento, respalda_evento
from app.services.validacion_embeddings_rag import (
    validar_configuracion_rag,
    validar_resultado_consulta,
)
from app.settings import Configuracion, obtener_configuracion

logger = logging.getLogger(__name__)


def _ordenar(fragmentos, limite):
    return sorted(fragmentos, key=lambda item: (-item.similitud, item.fragmento_id))[:limite]


def _resultado(primarios, config, complementarios=(), *, academico=False, tipo_documental=None):
    mejor = max((f.similitud for f in primarios), default=None)
    confianza = max(0.0, min(1.0, mejor)) if mejor is not None else 0.0
    umbral = config.umbral_rag_para_tipo(tipo_documental) if academico else config.rag_umbral_confianza
    return ResultadoRecuperacion(
        fragmentos=[*primarios, *complementarios], mejor_similitud=mejor,
        confianza=confianza, umbral=umbral,
        evidencia_suficiente=bool(primarios) and confianza >= umbral,
        fragmento_ids_primarios=[f.fragmento_id for f in primarios] if academico else None,
    )


def _es_primario(fragmento, filtros, modalidad):
    return (
        filtros.materia_id is not None and fragmento.materia_id == filtros.materia_id
        and fragmento.tipo_documental == filtros.tipo_documental and fragmento.vigente is True
        and filtros.vigente is not False
        and (filtros.periodo is None or fragmento.periodo is not None
             and fragmento.periodo.strip().upper() == filtros.periodo.strip().upper())
        and ((fragmento.comision is None or filtros.tipo_documental == "programa"
              and fragmento.comision == "TODAS") if modalidad == "comun" else
             filtros.comision is not None and fragmento.comision is not None
             and fragmento.comision.strip().upper() == filtros.comision.strip().upper())
    )


def _es_complementario(fragmento, filtros):
    if fragmento.vigente is not True or filtros.vigente is False:
        return False
    if fragmento.tipo_documental not in (filtros.tipo_documental, "general"):
        return False
    for campo in ("materia_id", "comision", "periodo"):
        valor, pedido = getattr(fragmento, campo), getattr(filtros, campo)
        if valor is not None and pedido is not None:
            if str(valor).strip().upper() != str(pedido).strip().upper():
                return False
    return True


async def _recuperar_academico(vector, filtros, limite, ids, config, *, evento=None, gestor=None):
    def resumir(primarios, complementarios=()):
        return _resultado(primarios, config, complementarios, academico=True,
                          tipo_documental=filtros.tipo_documental)

    async def buscar(modalidad, cantidad=limite, *, vector_busqueda=None):
        filas = await asyncio.to_thread(
            repositorio.buscar_fragmentos_similares,
            vector if vector_busqueda is None else vector_busqueda, filtros=filtros,
            top_k=cantidad, documento_ids=ids, modalidad=modalidad,
        )
        if modalidad != "complementaria":
            filas = [f for f in filas if _es_primario(f, filtros, modalidad)]
            if evento is not None:
                cantidad = len(filas)
                filas = [f for f in filas if respalda_evento(f.contenido, evento)]
                logger.debug("seleccion_evento tipo=%s numero=%s candidatos=%d compatibles=%d ids=%s",
                             evento.tipo, evento.numero if evento.numero is None or 0 <= evento.numero <= 99 else None,
                             cantidad, len(filas), [f.fragmento_id for f in filas])
        else:
            filas = [f for f in filas if _es_complementario(f, filtros)]
            if evento is not None:
                filas = [f for f in filas if f.tipo_documental == "general"
                         or respalda_evento(f.contenido, evento)]
        return _ordenar(filas, cantidad)

    modalidad = "comun" if filtros.tipo_documental == "programa" and not filtros.comision else "primaria"
    candidatos = max(limite, 20) if evento is not None else limite
    primarios = _ordenar(await buscar(modalidad, candidatos), limite)
    if evento is not None and not resumir(primarios).evidencia_suficiente:
        # Un segundo vector transitorio. Nunca reemplaza el embedding original de la FAQ.
        dirigido = await gestor.embed_query(consulta_dirigida(evento))
        validar_resultado_consulta(dirigido)
        adicionales = _ordenar(await buscar(modalidad, candidatos,
                                           vector_busqueda=dirigido.vector), limite)
        anterior = resumir(primarios).mejor_similitud
        nuevo = resumir(adicionales).mejor_similitud
        if nuevo is not None and (anterior is None or nuevo >= anterior):
            primarios = adicionales
            vector = dirigido.vector
    if filtros.tipo_documental == "programa" and filtros.comision:
        if not resumir(primarios).evidencia_suficiente:
            comunes = await buscar("comun")
            primarios = (comunes if resumir(comunes).evidencia_suficiente
                         else _ordenar([*primarios, *comunes], limite))
    complementarios = []
    if len(primarios) < limite and resumir(primarios).evidencia_suficiente:
        usados = {f.fragmento_id for f in primarios}
        filas = await buscar("complementaria", limite + len(primarios))
        complementarios = [f for f in filas if f.fragmento_id not in usados][:limite - len(primarios)]
    return resumir(primarios, complementarios)


async def recuperar_fragmentos(
    texto: str,
    *,
    filtros: FiltrosRecuperacion | None = None,
    top_k: int | None = None,
    documento_ids: Sequence[int] | None = None,
    gestor: GestorEmbeddings | None = None,
    configuracion: Configuracion | None = None,
    embedding_consulta: ResultadoEmbedding | None = None,
) -> ResultadoRecuperacion:
    if not isinstance(texto, str) or not texto.strip():
        raise ValueError("La consulta no puede estar vacía.")
    config = configuracion if configuracion is not None else obtener_configuracion()
    validar_configuracion_rag(config)
    limite = config.rag_top_k if top_k is None else top_k
    if isinstance(limite, bool) or not isinstance(limite, int) or limite <= 0:
        raise ValueError("Top K debe ser un entero positivo.")
    aplicados = filtros if filtros is not None else FiltrosRecuperacion()
    if not isinstance(aplicados, FiltrosRecuperacion):
        raise ValueError("Se esperaban filtros RAG tipados.")
    ids = list(documento_ids) if documento_ids is not None else None
    if ids is not None and any(
        isinstance(valor, bool) or not isinstance(valor, int) or valor <= 0 for valor in ids
    ):
        raise ValueError("Los identificadores de documento deben ser enteros positivos.")
    generador = gestor if gestor is not None else obtener_gestor_embeddings()
    resultado = (embedding_consulta if embedding_consulta is not None
                 else await generador.embed_query(texto.strip()))
    validar_resultado_consulta(resultado)
    if aplicados.tipo_documental in ("programa", "cronograma"):
        evento = identificar_evento(texto) if aplicados.tipo_documental == "cronograma" else None
        return await _recuperar_academico(resultado.vector, aplicados, limite, ids, config,
                                         evento=evento, gestor=generador)
    fragmentos = await asyncio.to_thread(
        repositorio.buscar_fragmentos_similares,
        resultado.vector,
        filtros=aplicados,
        top_k=limite,
        documento_ids=ids,
    )
    return _resultado(_ordenar(fragmentos, limite), config)
