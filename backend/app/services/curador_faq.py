"""T41/T43: carga explícita y regeneración acotada con el flujo documental compartido."""

import asyncio
import logging
import math
from datetime import UTC, date, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db import faq as repositorio
from app.db.repositorio import listar_materias_chat
from app.models.chat import ContextoChat, SolicitudChat
from app.models.faq import ContextoFaq, FaqCurada, FuenteFaq, ResumenCargaFaq, normalizar_comision
from app.providers.embeddings.base import ResultadoEmbedding
from app.services.conversacion import generar_documental
from app.services.sanitizador import normalizar_comparacion
from app.settings import MODELO_EMBEDDINGS_RAG, Configuracion

logger = logging.getLogger(__name__)


class EntradaFaq(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    pregunta_canonica: str = Field(min_length=1, max_length=2000)
    materia: int | str | None = None
    comision: str | None = None
    periodo: str | None = None
    vigencia_hasta: date | None = None

    @field_validator("comision")
    @classmethod
    def _comision(cls, valor):
        return normalizar_comision(valor)

    @field_validator("periodo")
    @classmethod
    def _periodo(cls, valor):
        return ContextoFaq(periodo=valor).periodo

    @field_validator("vigencia_hasta", mode="before")
    @classmethod
    def _fecha(cls, valor):
        return date.fromisoformat(valor) if isinstance(valor, str) else valor


async def _contexto(entrada: EntradaFaq) -> ContextoChat:
    materia_id = None
    if entrada.materia is not None:
        catalogo = await asyncio.to_thread(listar_materias_chat)
        coincidencias = [m for m in catalogo if (
            type(entrada.materia) is int and m.id == entrada.materia
            or isinstance(entrada.materia, str) and any(
                normalizar_comparacion(entrada.materia) == normalizar_comparacion(v)
                for v in (m.nombre, m.codigo)
            )
        )]
        if len(coincidencias) != 1:
            raise ValueError("La materia FAQ no identifica un registro inequívoco del catálogo.")
        materia_id = coincidencias[0].id
    return ContextoChat(materia_id=materia_id, comision=entrada.comision, periodo=entrada.periodo)


async def cargar_pregunta(
    entrada: EntradaFaq, *, configuracion: Configuracion, gestor_embeddings, gestor_llm,
    embedding_consulta: ResultadoEmbedding | None = None,
    fecha_limite: datetime | None = None,
) -> ResumenCargaFaq | None:
    """No escribe si el mismo flujo del chat no produce evidencia suficiente y verificable."""
    contexto = await _contexto(entrada)
    generacion = await generar_documental(
        SolicitudChat(mensaje=entrada.pregunta_canonica, contexto=contexto),
        configuracion=configuracion, gestor_embeddings=gestor_embeddings, gestor_llm=gestor_llm,
        embedding_consulta=embedding_consulta,
    )
    if generacion is None:
        return None
    primarios = generacion.primarios
    fuentes = {}
    for f in generacion.fuentes_versionadas:
        primaria = primarios is None or f.fragmento_id in primarios
        if f.drive_id not in fuentes:
            fuentes[f.drive_id] = FuenteFaq(
                drive_id=f.drive_id, fragmento_ids=[f.fragmento_id], es_primaria=primaria,
                version_documento=f.version_documento,
            ).model_dump()
        elif f.fragmento_id not in fuentes[f.drive_id]["fragmento_ids"]:
            fuentes[f.drive_id]["fragmento_ids"].append(f.fragmento_id)
        fuentes[f.drive_id]["es_primaria"] |= primaria
    # La especificidad proviene del documento utilizado, nunca se generaliza una fuente específica.
    filtros = generacion.filtros
    valores = {campo: getattr(filtros, campo) for campo in ("materia_id", "comision", "periodo")}
    for campo in valores:
        aplicables = {getattr(f, campo) for f in generacion.fuentes_versionadas
                      if (primarios is None or f.fragmento_id in primarios)
                      and getattr(f, campo) is not None}
        if valores[campo] is None and aplicables:
            if len(aplicables) != 1:
                return None
            valores[campo] = aplicables.pop()
    faq = FaqCurada(
        pregunta_canonica=entrada.pregunta_canonica,
        pregunta_normalizada=normalizar_comparacion(entrada.pregunta_canonica),
        respuesta=generacion.respuesta.respuesta, embedding=generacion.embedding.vector,
        tipo_documental=filtros.tipo_documental or "general",
        confianza_evidencia=generacion.respuesta.confianza,
        vigencia_hasta=entrada.vigencia_hasta, fecha_limite=fecha_limite, **valores,
    )
    if fecha_limite is not None and datetime.now(UTC) >= fecha_limite:
        raise TimeoutError("Se agotó el presupuesto de regeneración FAQ.")
    return await asyncio.to_thread(
        repositorio.guardar_faq_curada, faq,
        [FuenteFaq.model_validate(f) for f in fuentes.values()],
    )


async def regenerar_pendientes(
    *, configuracion: Configuracion, gestor_embeddings, gestor_llm,
    limite: int | None = None, presupuesto_segundos: float | None = None,
) -> dict[str, int]:
    """Fase independiente: falla de proveedor o timeout conserva pendientes y no revierte sync."""
    maximo = configuracion.faq_regeneracion_maximo if limite is None else limite
    presupuesto = (configuracion.faq_regeneracion_presupuesto_segundos
                   if presupuesto_segundos is None else presupuesto_segundos)
    if (type(maximo) is not int or maximo < 0 or isinstance(presupuesto, bool)
            or not math.isfinite(presupuesto) or presupuesto < 0):
        raise ValueError("Los límites de regeneración no pueden ser negativos.")
    resumen = {"procesadas": 0, "regeneradas": 0, "fallidas": 0, "diferidas": 0}
    if not maximo or not presupuesto:
        return resumen
    pendientes = []
    fecha_limite = datetime.now(UTC) + timedelta(seconds=presupuesto)
    try:
        async with asyncio.timeout(presupuesto):
            pendientes = (await asyncio.to_thread(repositorio.listar_faq_pendientes, maximo))[:maximo]
            for pendiente in pendientes:
                resumen["procesadas"] += 1
                try:
                    vector = (ResultadoEmbedding(pendiente.embedding, "gemini",
                                                 MODELO_EMBEDDINGS_RAG, 768)
                              if pendiente.embedding is not None else None)
                    resultado = await cargar_pregunta(
                        EntradaFaq(pregunta_canonica=pendiente.pregunta_canonica,
                                   materia=pendiente.materia_id, comision=pendiente.comision,
                                   periodo=pendiente.periodo, vigencia_hasta=pendiente.vigencia_hasta),
                        configuracion=configuracion, gestor_embeddings=gestor_embeddings,
                        gestor_llm=gestor_llm, embedding_consulta=vector,
                        fecha_limite=fecha_limite,
                    )
                    resumen["regeneradas" if resultado is not None else "fallidas"] += 1
                except Exception as error:
                    resumen["fallidas"] += 1
                    logger.warning("Regeneración FAQ pendiente: tipo=%s", type(error).__name__)
    except TimeoutError:
        logger.info("Regeneración FAQ diferida: presupuesto agotado.")
    except Exception as error:
        logger.warning("Regeneración FAQ diferida: tipo=%s", type(error).__name__)
        raise
    resumen["diferidas"] = len(pendientes) - resumen["regeneradas"] - resumen["fallidas"]
    return resumen
