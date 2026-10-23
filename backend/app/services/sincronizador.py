"""Indexación explícita de documentos de Drive sobre el índice RAG existente."""

import asyncio
import hashlib
import logging
from collections.abc import Mapping, Sequence

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.db.repositorio import obtener_documento_por_drive_id, reemplazar_fragmentos_documento
from app.drive.cliente import descargar_contenido_archivo
from app.drive.extractores import extraer_texto_desde_bytes
from app.models.rag import (
    DocumentoDrive,
    FragmentoParaIndexar,
    ResultadoIndexacionDocumento,
    ResultadoSincronizacion,
    ResumenIndexacion,
)
from app.providers.embeddings import EmbeddingsNoDisponibles, GestorEmbeddings
from app.providers.embeddings.dependencias import obtener_gestor_embeddings
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
    documento: DocumentoDrive, fragmentos: Sequence[FragmentoParaIndexar]
) -> ResumenIndexacion:
    """Reemplaza el documento y todos sus fragmentos mediante una única RPC atómica."""
    return reemplazar_fragmentos_documento(documento, fragmentos)


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
) -> ResultadoSincronizacion:
    """Reindexa cada documento explícitamente y conserva resultados por documento.

    Descarga, extracción y persistencia síncronas se delegan a hilos. El lote completo
    de embeddings se valida antes de crear o reemplazar cualquier fila en Supabase.
    Un error de un documento no impide procesar los restantes.
    """
    seleccion = _validar_documentos(documentos)
    if not seleccion:
        return ResultadoSincronizacion(resultados=[])
    config = configuracion if configuracion is not None else obtener_configuracion()
    validar_configuracion_rag(config)
    actual = gestor if gestor is not None else obtener_gestor_embeddings()
    resultados: list[ResultadoIndexacionDocumento] = []
    for documento in seleccion:
        categoria = "consulta_documento_fallida"
        try:
            # Es una lectura: la creación y el reemplazo se resuelven juntos en la RPC.
            await asyncio.to_thread(obtener_documento_por_drive_id, documento.drive_id)
            categoria = "descarga_fallida"
            contenido = await asyncio.to_thread(
                descargar_contenido_archivo,
                documento.drive_id,
                tipo_mime=documento.tipo_mime,
                estricto=True,
            )
            if not isinstance(contenido, bytes) or not contenido:
                raise ValueError("La descarga no devolvió contenido.")
            categoria = "extraccion_fallida"
            secciones = await asyncio.to_thread(
                extraer_texto_desde_bytes,
                contenido,
                documento.titulo,
                tipo_mime=documento.tipo_mime,
            )
            chunks = await asyncio.to_thread(_fragmentar_secciones, secciones, documento.titulo)
            categoria = "embeddings_fallidos"
            embeddings = await actual.embed_documents([chunk.page_content for chunk in chunks])
            validar_resultado_documentos(embeddings, len(chunks))
            fragmentos = [
                FragmentoParaIndexar(contenido=chunk.page_content, vector=vector, orden=orden)
                for orden, (chunk, vector) in enumerate(
                    zip(chunks, embeddings.vectores, strict=True), start=1
                )
            ]
            categoria = "persistencia_fallida"
            resumen = await asyncio.to_thread(sync_document_chunks, documento, fragmentos)
            resultados.append(
                ResultadoIndexacionDocumento(
                    drive_id=documento.drive_id, titulo=documento.titulo, resumen=resumen
                )
            )
        except Exception as error:
            if isinstance(error, ErrorEmbeddingIncompatible):
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
