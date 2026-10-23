"""Recuperación T31/T32: no genera respuestas ni llama a un LLM."""

import asyncio
from collections.abc import Sequence

from app.db import repositorio
from app.models.rag import FiltrosRecuperacion, ResultadoRecuperacion
from app.providers.embeddings.dependencias import obtener_gestor_embeddings
from app.providers.embeddings.gestor import GestorEmbeddings
from app.services.validacion_embeddings_rag import (
    validar_configuracion_rag,
    validar_resultado_consulta,
)
from app.settings import Configuracion, obtener_configuracion


async def recuperar_fragmentos(
    texto: str,
    *,
    filtros: FiltrosRecuperacion | None = None,
    top_k: int | None = None,
    documento_ids: Sequence[int] | None = None,
    gestor: GestorEmbeddings | None = None,
    configuracion: Configuracion | None = None,
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
    resultado = await generador.embed_query(texto.strip())
    validar_resultado_consulta(resultado)
    fragmentos = await asyncio.to_thread(
        repositorio.buscar_fragmentos_similares,
        resultado.vector,
        filtros=aplicados,
        top_k=limite,
        documento_ids=ids,
    )
    fragmentos = sorted(fragmentos, key=lambda item: (-item.similitud, item.fragmento_id))[:limite]
    mejor = fragmentos[0].similitud if fragmentos else None
    confianza = max(0.0, min(1.0, mejor)) if mejor is not None else 0.0
    return ResultadoRecuperacion(
        fragmentos=fragmentos,
        mejor_similitud=mejor,
        confianza=confianza,
        umbral=config.rag_umbral_confianza,
        evidencia_suficiente=bool(fragmentos) and confianza >= config.rag_umbral_confianza,
    )
