"""Indexación explícita de documentos de Drive sobre el índice RAG existente."""

import asyncio
import hashlib
import logging
from collections.abc import Mapping, Sequence
from uuid import uuid4

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.db.repositorio import obtener_documento_por_drive_id, reemplazar_fragmentos_documento
from app.db import repositorio
from app.drive.cliente import (
    descargar_contenido_archivo,
    listar_documentos_sincronizables,
    validar_version_descargada,
)
from app.drive.extractores import extraer_texto_desde_bytes
from app.models.rag import (
    DocumentoDrive,
    ErrorSincronizacion,
    FragmentoParaIndexar,
    MetadataDocumental,
    ResultadoIndexacionDocumento,
    ResultadoSincronizacion,
    ResumenIndexacion,
    ResumenSincronizacionDrive,
)
from app.providers.embeddings import EmbeddingsNoDisponibles, GestorEmbeddings
from app.providers.embeddings.dependencias import obtener_gestor_embeddings
from app.services.metadata_documental import (
    ErrorMetadataDocumental, MateriaCatalogo, resolver_metadata_documental,
)
from app.services.validacion_embeddings_rag import (
    ErrorEmbeddingIncompatible,
    validar_configuracion_rag,
    validar_resultado_documentos,
)
from app.settings import Configuracion, obtener_configuracion

logger = logging.getLogger(__name__)


def generate_file_hash(file_path: str) -> str:
    """Calcula una huella local; la indexación no persiste ni compara hashes."""
    resumen = hashlib.sha256()
    try:
        with open(file_path, "rb") as archivo:
            for bloque in iter(lambda: archivo.read(4096), b""):
                resumen.update(bloque)
    except FileNotFoundError:
        return ""
    return resumen.hexdigest()


def detect_file_status(file_path: str, current_hash: str, mock_db: dict) -> str:
    """Comparación local conservada para herramientas antiguas; no consulta la base."""
    if file_path not in mock_db:
        return "new"
    return "unchanged" if current_hash == mock_db[file_path] else "modified"


def split_text_into_chunks(
    text: str, document_title: str, section_name: str
) -> list[Document]:
    """Fragmenta una sección con máximo 1000 caracteres y solapamiento de 200."""
    if not isinstance(text, str):
        raise ValueError("El contenido de una sección debe ser texto.")
    if not text.strip():
        return []
    fragmentador = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
        length_function=len,
        separators=["\n\n", "\n", ".", " ", ""],
    )
    return [
        Document(
            page_content=contenido,
            metadata={"document_title": document_title, "section": section_name},
        )
        for contenido in fragmentador.split_text(text)
    ]


def sync_document_chunks(
    documento: DocumentoDrive, fragmentos: Sequence[FragmentoParaIndexar],
    *, ejecucion_id: str | None = None, metadata: MetadataDocumental | None = None,
) -> ResumenIndexacion:
    """Reemplaza el documento y todos sus fragmentos mediante una única RPC atómica."""
    opciones = {}
    if ejecucion_id is not None:
        opciones["ejecucion_id"] = ejecucion_id
    if metadata is not None:
        opciones["metadata"] = metadata
    return reemplazar_fragmentos_documento(documento, fragmentos, **opciones)


def _resolver_metadata(
    documento: DocumentoDrive, catalogo: Sequence[MateriaCatalogo], config: Configuracion,
) -> MetadataDocumental:
    metadata = resolver_metadata_documental(
        documento.titulo, catalogo,
        comision_max_longitud=config.metadata_comision_max_longitud,
        tipos_documentales=config.metadata_tipos_documentales,
    )
    try:
        metadata.tipo_documental
    except ValueError:
        raise ErrorMetadataDocumental("tipo_documental_no_soportado") from None
    return metadata


async def _inactivar_invalido(anterior, ejecucion_id):
    if anterior is None or (anterior.estado == "inactivo" and anterior.vigente is False):
        return False
    # La CLI explícita no posee un lease global: adquirirlo sólo para esta escritura.
    propietario = ejecucion_id or str(uuid4())
    propio = ejecucion_id is None
    if propio and not await asyncio.to_thread(
        repositorio.gestionar_bloqueo_sync, propietario, "adquirir",
    ):
        raise SincronizacionEnCurso("Ya hay una sincronización en curso.")
    try:
        return await asyncio.to_thread(
            repositorio.actualizar_estado_documento_sync, anterior.drive_id, False, propietario,
        )
    finally:
        if propio and not await asyncio.to_thread(
            repositorio.gestionar_bloqueo_sync, propietario, "liberar",
        ):
            raise RuntimeError("No se pudo liberar la concesión.")


def _validar_documentos(documentos: Sequence[DocumentoDrive]) -> list[DocumentoDrive]:
    if isinstance(documentos, str | bytes | Mapping) or not isinstance(documentos, Sequence):
        raise ValueError("Se esperaba una secuencia de DocumentoDrive.")
    lista = list(documentos)
    vistos: set[str] = set()
    for documento in lista:
        if not isinstance(documento, DocumentoDrive):
            raise ValueError("Todos los elementos deben ser DocumentoDrive.")
        for valor in (documento.drive_id, documento.titulo, documento.tipo_mime):
            if not isinstance(valor, str) or not valor.strip():
                raise ValueError("Los documentos requieren ID, título y tipo MIME.")
        if documento.drive_id in vistos:
            raise ValueError("La selección contiene identificadores de Drive repetidos.")
        vistos.add(documento.drive_id)
    return lista


def _fragmentar_secciones(secciones: object, titulo: str) -> list[Document]:
    if not isinstance(secciones, list):
        raise ValueError("La extracción debe devolver una lista de secciones.")
    fragmentos: list[Document] = []
    for seccion in secciones:
        if not isinstance(seccion, dict) or not isinstance(seccion.get("content"), str):
            raise ValueError("La extracción devolvió una sección inválida.")
        nombre = seccion.get("section") or "General"
        if not isinstance(nombre, str):
            raise ValueError("La extracción no produjo una sección indexable.")
        fragmentos.extend(split_text_into_chunks(seccion["content"], titulo, nombre))
    if not fragmentos:
        raise ValueError("El documento no contiene texto indexable.")
    return fragmentos


async def run_full_sync(
    documentos: Sequence[DocumentoDrive],
    *,
    gestor: GestorEmbeddings | None = None,
    configuracion: Configuracion | None = None,
    ejecucion_id: str | None = None,
    _catalogo: Sequence[MateriaCatalogo] | None = None,
) -> ResultadoSincronizacion:
    """Valida nombres y sólo indexa versiones nuevas; conserva resultados por documento.

    Descarga, extracción y persistencia síncronas se delegan a hilos. El lote completo
    de embeddings se valida antes de crear o reemplazar cualquier fila en Supabase.
    Un error de un documento no impide procesar los restantes.
    """
    seleccion = _validar_documentos(documentos)
    if not seleccion:
        return ResultadoSincronizacion(resultados=[])
    config = configuracion if configuracion is not None else obtener_configuracion()
    validar_configuracion_rag(config)
    catalogo = (_catalogo if _catalogo is not None
                else await asyncio.to_thread(repositorio.listar_materias_chat))
    actual = gestor
    resultados: list[ResultadoIndexacionDocumento] = []
    for documento in seleccion:
        categoria = "consulta_documento_fallida"
        try:
            # Es una lectura: la creación y el reemplazo se resuelven juntos en la RPC.
            anterior = await asyncio.to_thread(obtener_documento_por_drive_id, documento.drive_id)
            try:
                metadata = _resolver_metadata(documento, catalogo, config)
            except ErrorMetadataDocumental as error:
                categoria = "baja_nombre_invalido_fallida"
                await _inactivar_invalido(anterior, ejecucion_id)
                raise error
            if anterior is not None and not _version_distinta(documento, anterior):
                categoria = "actualizacion_metadata_fallida"
                await asyncio.to_thread(
                    repositorio.actualizar_metadata_documento_sync,
                    documento, metadata, ejecucion_id,
                )
                cantidad = await asyncio.to_thread(repositorio.contar_fragmentos_documento, anterior.id)
                resultados.append(ResultadoIndexacionDocumento(
                    drive_id=documento.drive_id, titulo=documento.titulo,
                    resumen=ResumenIndexacion(documento_id=anterior.id,
                                              fragmentos_indexados=cantidad,
                                              indexado_en=anterior.indexado_en),
                ))
                continue
            categoria = "descarga_fallida"
            contenido = await asyncio.to_thread(
                descargar_contenido_archivo,
                documento.drive_id,
                tipo_mime=documento.tipo_mime,
                estricto=True,
            )
            if not isinstance(contenido, bytes) or not contenido:
                raise ValueError("La descarga no devolvió contenido.")
            if documento.drive_modified_time is not None:
                categoria = "version_drive_modificada"
                await asyncio.to_thread(validar_version_descargada, documento, contenido)
            categoria = "extraccion_fallida"
            secciones = await asyncio.to_thread(
                extraer_texto_desde_bytes,
                contenido,
                documento.titulo,
                tipo_mime=documento.tipo_mime,
            )
            chunks = await asyncio.to_thread(_fragmentar_secciones, secciones, documento.titulo)
            categoria = "embeddings_fallidos"
            if actual is None:
                actual = obtener_gestor_embeddings()
            embeddings = await actual.embed_documents([chunk.page_content for chunk in chunks])
            validar_resultado_documentos(embeddings, len(chunks))
            fragmentos = [
                FragmentoParaIndexar(contenido=chunk.page_content, vector=vector, orden=orden)
                for orden, (chunk, vector) in enumerate(
                    zip(chunks, embeddings.vectores, strict=True), start=1
                )
            ]
            categoria = "persistencia_fallida"
            opciones = {"metadata": metadata}
            if ejecucion_id is not None:
                opciones["ejecucion_id"] = ejecucion_id
            resumen = await asyncio.to_thread(sync_document_chunks, documento, fragmentos, **opciones)
            resultados.append(
                ResultadoIndexacionDocumento(
                    drive_id=documento.drive_id, titulo=documento.titulo, resumen=resumen
                )
            )
        except Exception as error:
            if isinstance(error, ErrorMetadataDocumental):
                categoria = error.categoria
            elif isinstance(error, ErrorEmbeddingIncompatible):
                categoria = "embeddings_incompatibles"
            elif isinstance(error, EmbeddingsNoDisponibles):
                categoria = "embeddings_no_disponibles"
            logger.warning("No se pudo indexar un documento (%s).", categoria)
            resultados.append(
                ResultadoIndexacionDocumento(
                    drive_id=documento.drive_id, titulo=documento.titulo, error=categoria
                )
            )
    return ResultadoSincronizacion(resultados=resultados)


class SincronizacionEnCurso(RuntimeError):
    """Otra instancia conserva la concesión compartida."""


def _version_distinta(actual: DocumentoDrive, anterior) -> bool:
    if anterior.indexado_en is None or anterior.drive_modified_time is None:
        return True
    binario = actual.tipo_mime in {
        "application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    if binario and actual.drive_md5_checksum and anterior.drive_md5_checksum:
        return actual.drive_md5_checksum.lower() != anterior.drive_md5_checksum.lower()
    return actual.drive_modified_time is None or actual.drive_modified_time != anterior.drive_modified_time


def _metadata_distinta(documento, anterior, metadata) -> bool:
    return (
        anterior.titulo != documento.titulo or anterior.materia_id != metadata.materia_id
        or anterior.comision != metadata.comision or anterior.periodo != metadata.periodo
        or anterior.tipo_documental != metadata.tipo_documental
        or anterior.drive_modified_time != documento.drive_modified_time
        or (anterior.drive_md5_checksum or "").lower() != (documento.drive_md5_checksum or "").lower()
    )


async def sincronizar_drive_completo(
    *, gestor: GestorEmbeddings | None = None, configuracion: Configuracion | None = None,
) -> ResumenSincronizacionDrive:
    """Reconcilia por Drive ID; sólo indexa nuevos o versiones distintas.

    La concesión se renueva cada minuto y antes de cada escritura. Las RPC comprueban
    su vigencia en la transacción para impedir escrituras de un ejecutor vencido.
    """
    config = configuracion if configuracion is not None else obtener_configuracion()
    propietario = str(uuid4())
    adquirido = False
    detener = asyncio.Event()
    perdido = asyncio.Event()
    latido = None
    contadores = dict(drive_encontrados=0, nuevos=0, actualizados=0, inactivados=0,
                      reactivados=0, sin_cambios=0)
    errores = []
    fatal = False
    fase = "bloqueo_no_disponible"

    async def renovar():
        if perdido.is_set() or not await asyncio.to_thread(
            repositorio.gestionar_bloqueo_sync, propietario, "renovar",
        ):
            perdido.set()
            raise RuntimeError("Concesión perdida.")

    async def mantener_bloqueo():
        while not detener.is_set():
            try:
                await asyncio.wait_for(detener.wait(), timeout=60)
            except TimeoutError:
                try:
                    await renovar()
                except Exception:
                    perdido.set()
                    return

    try:
        adquirido = await asyncio.to_thread(
            repositorio.gestionar_bloqueo_sync, propietario, "adquirir",
        )
        if not adquirido:
            raise SincronizacionEnCurso("Ya hay una sincronización en curso.")
        latido = asyncio.create_task(mantener_bloqueo())
        fase = "listado_drive_fallido"
        documentos = await asyncio.to_thread(listar_documentos_sincronizables)
        documentos = _validar_documentos(documentos)
        if any(d.drive_modified_time is None for d in documentos):
            raise ValueError("Versión de Drive ausente.")
        contadores["drive_encontrados"] = len(documentos)
        fase = "catalogo_materias_fallido"
        catalogo = await asyncio.to_thread(repositorio.listar_materias_chat)
        fase = "lectura_corpus_fallida"
        persistidos = await asyncio.to_thread(repositorio.listar_documentos_sync)
        anteriores = {d.drive_id: d for d in persistidos}
        if len(anteriores) != len(persistidos):
            raise ValueError("Identidades duplicadas.")
        fase = "bloqueo_perdido"
        for documento in documentos:
            await renovar()
            anterior = anteriores.get(documento.drive_id)
            try:
                metadata = _resolver_metadata(documento, catalogo, config)
            except ErrorMetadataDocumental as error:
                errores.append(ErrorSincronizacion(drive_id=documento.drive_id,
                                                   categoria=error.categoria))
                try:
                    contadores["inactivados"] += int(await _inactivar_invalido(anterior, propietario))
                except Exception:
                    errores.append(ErrorSincronizacion(drive_id=documento.drive_id,
                                                       categoria="baja_nombre_invalido_fallida"))
                continue
            reactivar = anterior is not None and (
                anterior.estado != "activo" or anterior.vigente is not True
            )
            if anterior is None or _version_distinta(documento, anterior):
                resultado = await run_full_sync(
                    [documento], gestor=gestor, configuracion=config, ejecucion_id=propietario,
                    _catalogo=catalogo,
                )
                if resultado.errores or resultado.procesados != 1:
                    categoria = (resultado.resultados[0].error if resultado.resultados
                                 else None) or "indexacion_fallida"
                    errores.append(ErrorSincronizacion(
                        drive_id=documento.drive_id, categoria=categoria,
                    ))
                    continue
                contadores["nuevos" if anterior is None else "actualizados"] += 1
                if reactivar:
                    contadores["reactivados"] += 1
            elif reactivar or _metadata_distinta(documento, anterior, metadata):
                try:
                    cambiado = await asyncio.to_thread(
                        repositorio.actualizar_metadata_documento_sync,
                        documento, metadata, propietario,
                    )
                    if cambiado:
                        contadores["reactivados" if reactivar else "actualizados"] += 1
                except Exception:
                    errores.append(ErrorSincronizacion(
                        drive_id=documento.drive_id, categoria="actualizacion_estado_fallida",
                    ))
            else:
                contadores["sin_cambios"] += 1
        presentes = {d.drive_id for d in documentos}
        for anterior in persistidos:
            if anterior.drive_id in presentes:
                continue
            if anterior.estado == "inactivo" and anterior.vigente is False:
                continue
            await renovar()
            try:
                cambiado = await asyncio.to_thread(
                    repositorio.actualizar_estado_documento_sync,
                    anterior.drive_id, False, propietario,
                )
                contadores["inactivados"] += int(cambiado)
            except Exception:
                errores.append(ErrorSincronizacion(
                    drive_id=anterior.drive_id, categoria="baja_logica_fallida",
                ))
        await renovar()
    except SincronizacionEnCurso:
        raise
    except Exception:
        fatal = True
        errores.append(ErrorSincronizacion(categoria=fase))
    finally:
        detener.set()
        if latido is not None:
            await latido
        if adquirido:
            try:
                liberado = await asyncio.to_thread(
                    repositorio.gestionar_bloqueo_sync, propietario, "liberar",
                )
                if not liberado:
                    raise RuntimeError("Concesión perdida.")
            except Exception:
                errores.append(ErrorSincronizacion(categoria="liberacion_bloqueo_fallida"))
    for error in errores:
        logger.warning("Sincronización Drive: %s.", error.categoria)
    return ResumenSincronizacionDrive(
        **contadores, estado="error" if fatal else "parcial" if errores else "ok",
        errores=len(errores), detalles=errores,
    )
