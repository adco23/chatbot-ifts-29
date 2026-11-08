"""Acceso FAQ síncrono e inyectable. Nunca registra payloads ni credenciales."""

import math
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from supabase import Client

from app.db.conexion import obtener_cliente_supabase
from app.models.chat import ContextoChat
from app.models.faq import (
    CoincidenciaFaq,
    ContextoFaq,
    FaqCurada,
    FaqPendiente,
    FragmentoFuenteFaq,
    FuenteFaq,
    ResumenCargaFaq,
)
from app.services.validacion_embeddings_rag import validar_vector_rag


class ErrorRepositorioFaq(RuntimeError):
    """Error técnico distinto de un MISS; no conserva detalles remotos."""


def _cliente(cliente: Client | None) -> Client:
    seleccionado = cliente if cliente is not None else obtener_cliente_supabase()
    if seleccionado is None:
        raise ErrorRepositorioFaq("El repositorio FAQ no está disponible.")
    return seleccionado


def _filas(operacion: Callable[[], Any]) -> list[dict[str, Any]]:
    try:
        datos = operacion().data
        if not isinstance(datos, list) or any(not isinstance(fila, dict) for fila in datos):
            raise ValueError("Respuesta no tabular.")
        return datos
    except Exception:
        raise ErrorRepositorioFaq("No se pudo completar la operación FAQ.") from None


def _id(valor: int) -> None:
    if type(valor) is not int or valor <= 0:
        raise ValueError("Se esperaba una identidad FAQ positiva.")


def _umbral(valor: float) -> None:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise ValueError("Umbral FAQ inválido.")
    if not math.isfinite(valor) or not 0 <= valor <= 1:
        raise ValueError("Umbral FAQ inválido.")


def buscar_en_cache_faq(
    vector_consulta: Sequence[float], *, contexto: ContextoChat, tipo_documental: str,
    umbral: float, umbral_evidencia: float, cliente: Client | None = None,
) -> CoincidenciaFaq | None:
    validar_vector_rag(vector_consulta)
    _umbral(umbral)
    _umbral(umbral_evidencia)
    filtros = ContextoFaq(
        materia_id=contexto.materia_id, comision=contexto.comision,
        periodo=contexto.periodo, tipo_documental=tipo_documental,
    )
    base = _cliente(cliente)
    filas = _filas(lambda: base.schema("public").rpc("match_pregunta_frecuente", {
        "p_query_embedding": list(vector_consulta), "p_umbral": float(umbral),
        "p_materia_id": filtros.materia_id, "p_comision": filtros.comision,
        "p_periodo": filtros.periodo, "p_tipo_documental": filtros.tipo_documental,
        "p_umbral_evidencia": float(umbral_evidencia),
    }).execute())
    try:
        if len(filas) > 1:
            raise ValueError("Match FAQ no único.")
        return CoincidenciaFaq.model_validate(filas[0]) if filas else None
    except Exception:
        raise ErrorRepositorioFaq("La coincidencia FAQ no respeta el contrato.") from None


def obtener_fuentes_para_guardar(
    fragmento_ids: Sequence[int], *, cliente: Client | None = None,
) -> list[FragmentoFuenteFaq]:
    ids = list(fragmento_ids)
    for identidad in ids:
        _id(identidad)
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("Se esperaba evidencia FAQ inequívoca.")
    base = _cliente(cliente)
    filas = _filas(lambda: base.schema("public").rpc("obtener_fuentes_faq", {
        "p_fragmento_ids": ids,
    }).execute())
    try:
        fuentes = [FragmentoFuenteFaq.model_validate(fila) for fila in filas]
        if {f.fragmento_id for f in fuentes} != set(ids) or len(fuentes) != len(ids):
            raise ValueError("La evidencia cambió o desapareció.")
        return fuentes
    except Exception:
        raise ErrorRepositorioFaq(
            "La evidencia FAQ no está disponible íntegramente.",
        ) from None


def obtener_evidencia_faq(
    pregunta_id: int, generada_en_esperada: datetime, *, cliente: Client | None = None,
) -> list[FragmentoFuenteFaq]:
    _id(pregunta_id)
    if (not isinstance(generada_en_esperada, datetime)
            or generada_en_esperada.utcoffset() is not None):
        raise ValueError("Se requiere la generación FAQ esperada sin zona horaria.")
    base = _cliente(cliente)
    filas = _filas(lambda: base.schema("public").rpc("obtener_fuentes_cache_faq", {
        "p_pregunta_id": pregunta_id,
        "p_generada_en_esperada": generada_en_esperada.isoformat(),
    }).execute())
    try:
        fuentes = [FragmentoFuenteFaq.model_validate(fila) for fila in filas]
        if len({f.fragmento_id for f in fuentes}) != len(fuentes):
            raise ValueError("Evidencia FAQ ambigua.")
        return fuentes
    except Exception:
        raise ErrorRepositorioFaq("Las fuentes FAQ no respetan el contrato.") from None


def guardar_faq_curada(
    faq: FaqCurada, fuentes: Sequence[FuenteFaq], *, cliente: Client | None = None,
) -> ResumenCargaFaq:
    if not isinstance(faq, FaqCurada):
        raise ValueError("Se esperaba una FAQ curada tipada.")
    lista = list(fuentes)
    if not lista or any(not isinstance(fuente, FuenteFaq) for fuente in lista):
        raise ValueError("Se requieren fuentes FAQ tipadas.")
    if len({f.drive_id for f in lista}) != len(lista):
        raise ValueError("No se pueden repetir documentos fuente.")
    base = _cliente(cliente)
    filas = _filas(lambda: base.schema("public").rpc("guardar_faq_curada", {
        "p_faq": faq.model_dump(mode="json"),
        "p_fuentes": [fuente.model_dump(mode="json") for fuente in lista],
    }).execute())
    try:
        if len(filas) != 1:
            raise ValueError("Resumen FAQ no único.")
        return ResumenCargaFaq.model_validate(filas[0])
    except Exception:
        raise ErrorRepositorioFaq("El resultado de carga FAQ no respeta el contrato.") from None


def incrementar_acierto_cache_faq(
    pregunta_id: int, *, cliente: Client | None = None,
) -> None:
    _id(pregunta_id)
    base = _cliente(cliente)
    try:
        base.schema("public").rpc("incrementar_acierto_faq", {
            "p_id": pregunta_id,
        }).execute()
    except Exception:
        raise ErrorRepositorioFaq("No se pudo registrar el acierto FAQ.") from None


def listar_faq_pendientes(
    limite: int, *, cliente: Client | None = None,
) -> list[FaqPendiente]:
    _id(limite)
    base = _cliente(cliente)
    filas = _filas(lambda: base.schema("public").table("pregunta_frecuente").select(
        "id,pregunta_canonica,embedding,materia_id,comision,periodo,tipo_documental,vigencia_hasta"
    ).eq("estado", "pendiente").order("id").limit(limite).execute())
    try:
        return [FaqPendiente.model_validate(fila) for fila in filas]
    except Exception:
        raise ErrorRepositorioFaq("Las pendientes FAQ no respetan el contrato.") from None
