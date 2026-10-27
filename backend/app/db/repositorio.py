import hashlib
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from supabase import Client

from app.db.conexion import obtener_cliente_supabase
from app.models.rag import (
    DocumentoDrive,
    DocumentoPersistido,
    FiltrosRecuperacion,
    FragmentoParaIndexar,
    FragmentoRecuperado,
    MetadataDocumental,
    ResumenIndexacion,
    validar_vector_rag,
)


def obtener_materias_repo():
    """Recupera el listado oficial de materias desde Supabase (T10/T11)."""
    supabase = obtener_cliente_supabase()
    if not supabase:
        return []
    try:
        response = supabase.table("materia").select("*").execute()
        return response.data
    except Exception as e:
        print(f"Error al consultar materias en Supabase: {e}")
        return []

def registrar_consulta_anonima(texto_sanitizado: str, resultado: str, confianza: float = 0.0):
    """Registra de forma anónima las consultas procesadas para las métricas (RF09)."""
    supabase = obtener_cliente_supabase()
    if not supabase:
        return
    try:
        data = {
            "texto_sanitizado": texto_sanitizado,
            "resultado": resultado,
            "confianza": confianza
        }
        supabase.table("consulta").insert(data).execute()
    except Exception as e:
        print(f"Error al guardar métrica en Supabase: {e}")

def guardar_documento_institucional(
    titulo: str, drive_id: str, vigente: bool = True, estado: str = "activo"
):
    """Inserta o registra un documento institucional sincronizado desde Drive."""
    supabase = obtener_cliente_supabase()
    if not supabase:
        return None
    try:
        data = {
            "titulo": titulo,
            "drive_id": drive_id,
            "vigente": vigente,
            "estado": estado
        }
        # Forzamos el esquema public explícitamente por si PostgREST no lo resuelve por defecto
        response = supabase.schema("public").table("documento").insert(data).execute()
        return response.data[0] if response.data else None
    except Exception as e:
        print(f"Error al guardar documento en Supabase: {e}")
        return None

def guardar_fragmento_documento(
    documento_id: int,
    contenido: str,
    vector: list[float] | None = None,
    orden: int = 1,
):
    """Inserta un fragmento de texto asociado a un documento, con su embedding."""
    supabase = obtener_cliente_supabase()
    if not supabase:
        return None

    try:
        data = {
            "documento_id": documento_id,
            "contenido": contenido,
            "orden": orden,
            "vector": vector,
        }

        response = supabase.table("fragmento").insert(data).execute()
        return response.data[0] if response.data else None

    except Exception as e:
        print(f"Error al guardar fragmento en Supabase: {e}")
        return None


class ErrorRepositorio(RuntimeError):
    """Una operación RAG falló; no equivale a una búsqueda sin coincidencias."""


def listar_materias_chat(*, cliente: Client | None = None):
    """Catálogo estricto para resolver nombres sin fijar IDs en código."""
    from app.models.chat import MateriaChat

    base = _cliente_rag(cliente)
    materias = []
    ultimo_id = 0
    while True:
        filas = _ejecutar_rag(lambda: base.table("materia").select("id,codigo,nombre")
                             .gt("id", ultimo_id).order("id").limit(500).execute())
        try:
            pagina = [MateriaChat.model_validate(fila) for fila in filas]
            ids = [m.id for m in pagina]
            if ids != sorted(set(ids)) or any(i <= ultimo_id for i in ids):
                raise ValueError("Paginación inválida.")
        except Exception:
            raise ErrorRepositorio("El catálogo de materias no es válido.") from None
        materias.extend(pagina)
        if not pagina:
            return materias
        ultimo_id = pagina[-1].id


def listar_documentos_chat(*, cliente: Client | None = None) -> list[DocumentoPersistido]:
    """Cobertura documental activa/vigente; no es un catálogo de comisiones."""
    base = _cliente_rag(cliente)
    filas = _ejecutar_rag(lambda: base.table("documento").select(
        "id,titulo,drive_id,materia_id,comision,periodo,vigente,estado,indexado_en,tipo_documental"
    ).eq("estado", "activo").eq("vigente", True).execute())
    try:
        return [DocumentoPersistido.model_validate(fila) for fila in filas]
    except Exception:
        raise ErrorRepositorio("Los metadatos documentales no son válidos.") from None


def _cliente_rag(cliente: Client | None) -> Client:
    seleccionado = cliente if cliente is not None else obtener_cliente_supabase()
    if seleccionado is None:
        raise ErrorRepositorio("La base de datos no está disponible para RAG.")
    return seleccionado


def _ejecutar_rag(operacion: Callable[[], Any]) -> list[dict[str, Any]]:
    try:
        datos = operacion().data
        if not isinstance(datos, list) or any(not isinstance(fila, dict) for fila in datos):
            raise ValueError("Respuesta no tabular.")
        return datos
    except Exception:
        # No copiar mensajes del cliente: pueden contener payloads o credenciales.
        raise ErrorRepositorio("No se pudo completar la operación de datos RAG.") from None


def obtener_documento_por_drive_id(
    drive_id: str, *, cliente: Client | None = None
) -> DocumentoPersistido | None:
    if not isinstance(drive_id, str) or not drive_id.strip():
        raise ValueError("El identificador de Drive no puede estar vacío.")
    base = _cliente_rag(cliente)
    filas = _ejecutar_rag(
        lambda: (
            base.schema("public")
            .table("documento")
            .select("id,titulo,drive_id,materia_id,comision,periodo,vigente,estado,indexado_en,"
                    "drive_modified_time,drive_md5_checksum,tipo_documental")
            .eq("drive_id", drive_id.strip())
            .limit(1)
            .execute()
        )
    )
    if not filas:
        return None
    try:
        return DocumentoPersistido.model_validate(filas[0])
    except Exception:
        raise ErrorRepositorio("La respuesta de documento no respeta el contrato RAG.") from None


def _validar_id(documento_id: int) -> None:
    if isinstance(documento_id, bool) or not isinstance(documento_id, int) or documento_id <= 0:
        raise ValueError("El identificador de documento debe ser un entero positivo.")


def eliminar_fragmentos_documento(documento_id: int, *, cliente: Client | None = None) -> None:
    """Operación aislada; la reindexación utiliza la RPC atómica en su lugar."""
    _validar_id(documento_id)
    base = _cliente_rag(cliente)
    _ejecutar_rag(
        lambda: (
            base.schema("public")
            .table("fragmento")
            .delete()
            .eq("documento_id", documento_id)
            .execute()
        )
    )


def actualizar_documento_indexado(documento_id: int, *, cliente: Client | None = None) -> None:
    """Marca UTC sin zona, como la columna existente; no compone la reindexación."""
    _validar_id(documento_id)
    base = _cliente_rag(cliente)
    instante = datetime.now(UTC).replace(tzinfo=None).isoformat()
    filas = _ejecutar_rag(
        lambda: (
            base.schema("public")
            .table("documento")
            .update({"indexado_en": instante})
            .eq("id", documento_id)
            .execute()
        )
    )
    if not filas:
        raise ErrorRepositorio("No se encontró el documento para actualizar su indexación.")


def reemplazar_fragmentos_documento(
    documento: DocumentoDrive,
    fragmentos: Sequence[FragmentoParaIndexar],
    *,
    cliente: Client | None = None,
    ejecucion_id: str | None = None,
    metadata: MetadataDocumental | None = None,
) -> ResumenIndexacion:
    """Una única transacción SQL conserva el documento y sustituye todo su índice."""
    if not isinstance(documento, DocumentoDrive):
        raise ValueError("Se esperaba un documento de Drive tipado.")
    lote = list(fragmentos)
    if not lote or any(not isinstance(fragmento, FragmentoParaIndexar) for fragmento in lote):
        raise ValueError("Se esperaba un lote no vacío de fragmentos tipados.")
    if [fragmento.orden for fragmento in lote] != list(range(1, len(lote) + 1)):
        raise ValueError("Los fragmentos deben tener orden consecutivo desde uno.")
    for fragmento in lote:
        validar_vector_rag(fragmento.vector)
    base = _cliente_rag(cliente)
    parametros = {
        "p_drive_id": documento.drive_id,
        "p_titulo": documento.titulo,
        "p_fragmentos": [fragmento.model_dump() for fragmento in lote],
        "p_drive_modified_time": (
            documento.drive_modified_time.isoformat() if documento.drive_modified_time else None
        ),
        "p_drive_md5_checksum": documento.drive_md5_checksum,
        "p_propietario": ejecucion_id,
    }
    nombre_rpc = "reemplazar_fragmentos_documento_versionado"
    if metadata is not None:
        parametros.update(_parametros_metadata(metadata))
        nombre_rpc = "reemplazar_fragmentos_documento_tipado"
    filas = _ejecutar_rag(
        lambda: base.schema("public").rpc(
            nombre_rpc, parametros,
        ).execute()
    )
    try:
        if len(filas) != 1:
            raise ValueError("Resumen no único.")
        resumen = ResumenIndexacion.model_validate(filas[0])
        if resumen.fragmentos_indexados != len(lote):
            raise ValueError("Cantidad de fragmentos inconsistente.")
        return resumen
    except Exception:
        raise ErrorRepositorio("La respuesta de indexación no respeta el contrato RAG.") from None


def _parametros_metadata(metadata: MetadataDocumental) -> dict[str, Any]:
    if not isinstance(metadata, MetadataDocumental):
        raise ValueError("Se requiere metadata resuelta.")
    return {"p_materia_id": metadata.materia_id, "p_comision": metadata.comision,
            "p_periodo": metadata.periodo, "p_tipo_documental": metadata.tipo_documental}


def actualizar_metadata_documento_sync(
    documento: DocumentoDrive, metadata: MetadataDocumental,
    ejecucion_id: str | None, *, cliente: Client | None = None,
) -> bool:
    """Actualiza metadata y versión por Drive ID sin tocar fragmentos/indexado_en."""
    parametros = {
        "p_drive_id": documento.drive_id, "p_titulo": documento.titulo,
        **_parametros_metadata(metadata),
        "p_drive_modified_time": (
            documento.drive_modified_time.isoformat() if documento.drive_modified_time else None
        ),
        "p_drive_md5_checksum": documento.drive_md5_checksum, "p_propietario": ejecucion_id,
    }
    base = _cliente_rag(cliente)
    filas = _ejecutar_rag(lambda: base.schema("public").rpc(
        "actualizar_metadata_documento_tipado", parametros,
    ).execute())
    if len(filas) != 1 or type(filas[0].get("cambiado")) is not bool:
        raise ErrorRepositorio("Respuesta de actualización de metadata inválida.")
    return filas[0]["cambiado"]


def listar_documentos_sync(*, cliente: Client | None = None) -> list[DocumentoPersistido]:
    """Todos los registros del corpus institucional, sin truncamiento PostgREST."""
    base = _cliente_rag(cliente)
    documentos = []
    ultimo_id = 0
    while True:
        filas = _ejecutar_rag(lambda: base.schema("public").table("documento").select(
            "id,titulo,drive_id,materia_id,comision,periodo,vigente,estado,indexado_en,"
            "drive_modified_time,drive_md5_checksum,tipo_documental"
        ).gt("id", ultimo_id).order("id").limit(500).execute())
        if not filas:
            return documentos
        try:
            pagina = [DocumentoPersistido.model_validate(fila) for fila in filas]
            if any(d.id <= ultimo_id for d in pagina):
                raise ValueError("Paginación inválida.")
            if [d.id for d in pagina] != sorted({d.id for d in pagina}):
                raise ValueError("Orden inválido.")
        except Exception:
            raise ErrorRepositorio("El snapshot del corpus no es válido.") from None
        documentos.extend(pagina)
        ultimo_id = pagina[-1].id


def gestionar_bloqueo_sync(
    ejecucion_id: str, accion: str, *, cliente: Client | None = None,
) -> bool:
    if accion not in {"adquirir", "renovar", "liberar"}:
        raise ValueError("Acción de bloqueo inválida.")
    base = _cliente_rag(cliente)
    filas = _ejecutar_rag(lambda: base.schema("public").rpc("gestionar_bloqueo_sync", {
        "p_propietario": ejecucion_id, "p_accion": accion,
    }).execute())
    if len(filas) != 1 or type(filas[0].get("adquirido")) is not bool:
        raise ErrorRepositorio("Respuesta de bloqueo inválida.")
    return filas[0]["adquirido"]


def actualizar_estado_documento_sync(
    drive_id: str, activo: bool, ejecucion_id: str, *, titulo: str | None = None,
    cliente: Client | None = None,
) -> bool:
    base = _cliente_rag(cliente)
    filas = _ejecutar_rag(lambda: base.schema("public").rpc("actualizar_estado_documento_sync", {
        "p_drive_id": drive_id, "p_activo": activo, "p_propietario": ejecucion_id,
        "p_titulo": titulo,
    }).execute())
    if len(filas) != 1 or type(filas[0].get("cambiado")) is not bool:
        raise ErrorRepositorio("Respuesta de actualización inválida.")
    return filas[0]["cambiado"]


def contar_fragmentos_documento(documento_id: int, *, cliente: Client | None = None) -> int:
    _validar_id(documento_id)
    base = _cliente_rag(cliente)
    try:
        cantidad = base.schema("public").table("fragmento").select(
            "id", count="exact", head=True,
        ).eq("documento_id", documento_id).execute().count
        if type(cantidad) is not int or cantidad < 0:
            raise ValueError("Conteo inválido.")
        return cantidad
    except Exception:
        raise ErrorRepositorio("No se pudo contar los fragmentos del documento.") from None


def obtener_firmas_contenido_documentos(
    documento_ids: Sequence[int], *, cliente: Client | None = None,
) -> dict[int, str]:
    """Compara texto indexado completo, sin vectores, por documento y orden.

    Sólo se usa para resolver programas de varias comisiones. No equipara documentos
    sin texto ni hace inferencias semánticas. Los errores de conexión son explícitos.
    """
    ids = list(dict.fromkeys(documento_ids))
    for documento_id in ids:
        _validar_id(documento_id)
    if not ids:
        return {}
    base = _cliente_rag(cliente)
    fragmentos = {identidad: [] for identidad in ids}
    ultimo_id = 0
    while True:
        filas = _ejecutar_rag(lambda: base.schema("public").table("fragmento").select(
            "id,documento_id,orden,contenido"
        ).in_("documento_id", ids).gt("id", ultimo_id).order("id").limit(500).execute())
        if not filas:
            break
        for fila in filas:
            identidad, documento, orden, contenido = (
                fila.get("id"), fila.get("documento_id"), fila.get("orden"), fila.get("contenido"),
            )
            if (type(identidad) is not int or identidad <= ultimo_id or documento not in fragmentos
                    or type(orden) is not int or orden <= 0 or not isinstance(contenido, str)):
                raise ErrorRepositorio("Fragmentos inválidos al comparar programas.")
            fragmentos[documento].append((orden, identidad, contenido))
            ultimo_id = identidad
    firmas = {}
    for documento, lote in fragmentos.items():
        texto = " ".join(" ".join(f[2].split()) for f in sorted(lote))
        if texto.strip():
            firmas[documento] = hashlib.sha256(texto.encode("utf-8")).hexdigest()
    return firmas


def buscar_fragmentos_similares(
    vector_consulta: Sequence[float],
    *,
    filtros: FiltrosRecuperacion | None = None,
    top_k: int,
    documento_ids: Sequence[int] | None = None,
    cliente: Client | None = None,
    modalidad: str = "primaria",
) -> list[FragmentoRecuperado]:
    validar_vector_rag(vector_consulta)
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k <= 0:
        raise ValueError("Top K debe ser un entero positivo.")
    ids = list(documento_ids) if documento_ids is not None else None
    if ids is not None:
        for documento_id in ids:
            _validar_id(documento_id)
    aplicados = filtros if filtros is not None else FiltrosRecuperacion()
    if not isinstance(aplicados, FiltrosRecuperacion):
        raise ValueError("Se esperaban filtros RAG tipados.")
    base = _cliente_rag(cliente)
    parametros = {
        "p_query_embedding": list(vector_consulta),
        "p_top_k": top_k,
        "p_materia_id": aplicados.materia_id,
        "p_comision": aplicados.comision,
        "p_periodo": aplicados.periodo,
        "p_vigente": aplicados.vigente,
        "p_documento_ids": ids,
    }
    nombre_rpc = "match_fragmentos"
    if aplicados.tipo_documental in ("programa", "cronograma"):
        if modalidad not in ("primaria", "comun", "complementaria"):
            raise ValueError("Modalidad académica inválida.")
        nombre_rpc = "match_fragmentos_academicos"
        parametros.update(p_tipo_documental=aplicados.tipo_documental, p_modalidad=modalidad)
    filas = _ejecutar_rag(
        lambda: base.schema("public").rpc(nombre_rpc, parametros).execute()
    )
    try:
        return [FragmentoRecuperado.model_validate(fila) for fila in filas]
    except Exception:
        raise ErrorRepositorio("La respuesta de búsqueda no respeta el contrato RAG.") from None
