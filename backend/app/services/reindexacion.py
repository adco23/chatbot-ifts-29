"""Reindexación explícita de un documento; preparación/RPC compartidas con sync."""

import asyncio
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from app.db import repositorio
from app.drive.cliente import obtener_metadatos_archivo
from app.models.rag import DocumentoDrive, DocumentoPersistido, FragmentoPersistido
from app.providers.embeddings.dependencias import obtener_gestor_embeddings
from app.services import sincronizador
from app.services.comisiones import cargar_catalogo
from app.services.eventos_cronograma import (
    ErrorEvento, EventoCronograma, bloques_evento, validar_respuesta_evento,
)
from app.services.temporalidad import fechas_explicitas, fechas_respaldadas
from app.services.validacion_embeddings_rag import validar_configuracion_rag
from app.settings import obtener_configuracion

logger = logging.getLogger(__name__)
RAIZ_REPOSITORIO = Path(__file__).resolve().parents[3]
INTERVALO_RENOVACION = 60
FORMATO_RESPALDO = "ifts29_reindexacion_v1"


class ErrorReindexacion(RuntimeError):
    """La categoría es local y nunca contiene contenido, excepciones remotas o claves."""

    def __init__(self, categoria):
        self.categoria = categoria
        super().__init__(categoria)


def _ruta_respaldo(ruta, *, leer=False):
    if ruta is None:
        raise ErrorReindexacion("respaldo_requerido")
    destino = Path(ruta).expanduser().resolve()
    if destino.is_relative_to(RAIZ_REPOSITORIO):
        raise ErrorReindexacion("respaldo_dentro_del_repositorio")
    if leer:
        if not destino.is_file():
            raise ErrorReindexacion("respaldo_no_disponible")
    elif destino.exists() or not destino.parent.is_dir():
        raise ErrorReindexacion("ruta_respaldo_no_disponible")
    return destino


def validar_pfo_exigidas(contenidos, exigidas):
    """Identidad y rango deben coexistir en un chunk; no concatena ni infiere."""
    reporte = []
    for numero in exigidas:
        evento = EventoCronograma("pfo", numero)
        ordenes = []
        for orden, contenido in enumerate(contenidos, start=1):
            for bloque in bloques_evento(contenido, evento):
                dias = []
                for fecha in fechas_explicitas(bloque.texto):
                    if re.search(r"\d[/-]|\d de ", fecha) and not any(
                        fechas_respaldadas(fecha, [otra]) or fechas_respaldadas(otra, [fecha])
                        for otra in dias
                    ):
                        dias.append(fecha)
                if len(dias) != 2:
                    continue
                try:
                    validar_respuesta_evento(bloque.texto, contenidos, evento)
                except ErrorEvento:
                    raise ErrorReindexacion("pfo_rango_incompatible_o_ambiguo") from None
                ordenes.append(orden)
        if not ordenes:
            raise ErrorReindexacion("pfo_requerida_sin_rango_explicito")
        reporte.append({"identificador": f"PFO {numero}", "ordenes": sorted(set(ordenes)), "valida": True})
    return reporte


def _validar_identidad(documento, drive_id, documento_id, titulo):
    if documento is None or documento.id != documento_id or documento.drive_id != drive_id:
        raise ErrorReindexacion("identidad_no_coincide")
    if documento.titulo != titulo:
        raise ErrorReindexacion("titulo_no_coincide")
    if documento.estado != "activo" or documento.vigente is not True:
        raise ErrorReindexacion("documento_no_activo_vigente")
    if documento.indexado_en is None:
        raise ErrorReindexacion("documento_no_indexado")


async def _leer_objetivo(drive_id, documento_id, titulo, config):
    anterior = await asyncio.to_thread(repositorio.obtener_documento_por_drive_id, drive_id)
    _validar_identidad(anterior, drive_id, documento_id, titulo)
    materias = await asyncio.to_thread(repositorio.listar_materias_chat)
    # Esta resolución no ejecuta la rama de baja por nombre inválido del sync.
    candidato = DocumentoDrive(drive_id=drive_id, titulo=titulo, tipo_mime="application/pdf")
    metadata = sincronizador._resolver_metadata(candidato, materias, config)
    if any(getattr(anterior, campo) != getattr(metadata, campo) for campo in (
        "materia_id", "comision", "periodo", "tipo_documental",
    )):
        raise ErrorReindexacion("metadata_no_coincide")
    catalogo = await asyncio.to_thread(cargar_catalogo)
    if not catalogo.documento_habilitado(anterior, metadata.periodo):
        raise ErrorReindexacion("contexto_fuera_del_catalogo")
    return anterior, metadata


async def _preparar(anterior, metadata):
    datos = await asyncio.to_thread(obtener_metadatos_archivo, anterior.drive_id)
    documento = DocumentoDrive(
        drive_id=datos["id"], titulo=datos["name"], tipo_mime=datos["mimeType"],
        drive_modified_time=datos.get("modifiedTime"), drive_md5_checksum=datos.get("md5Checksum"),
    )
    if documento.drive_id != anterior.drive_id or documento.titulo != anterior.titulo:
        raise ErrorReindexacion("identidad_drive_no_coincide")
    if datos.get("trashed") is not False or documento.tipo_mime not in (
        "application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.google-apps.document",
    ):
        raise ErrorReindexacion("documento_drive_no_disponible")
    if (documento.drive_modified_time is None
            or documento.drive_modified_time != anterior.drive_modified_time
            or (documento.drive_md5_checksum or "").lower() != (anterior.drive_md5_checksum or "").lower()):
        raise ErrorReindexacion("version_drive_no_coincide")
    return await sincronizador.preparar_documento_indexacion(documento, metadata, anterior=anterior)


def _guardar_respaldo(ruta, anterior, fragmentos, pfo):
    datos = {"formato": FORMATO_RESPALDO, "documento": anterior.model_dump(mode="json"),
             "fragmentos": [f.model_dump(mode="json") for f in fragmentos], "pfo_exigidas": list(pfo)}
    contenido = json.dumps(datos, ensure_ascii=False, allow_nan=False).encode("utf-8")
    # O_EXCL impide sobrescribir otro respaldo. Nunca se almacena configuración.
    descriptor = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as archivo:
        archivo.write(contenido)
        archivo.flush()
        os.fsync(archivo.fileno())


def _leer_respaldo(ruta):
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        if not isinstance(datos, dict) or set(datos) != {"formato", "documento", "fragmentos", "pfo_exigidas"}:
            raise ValueError
        if datos["formato"] != FORMATO_RESPALDO or not isinstance(datos["fragmentos"], list):
            raise ValueError
        anterior = DocumentoPersistido.model_validate(datos["documento"])
        fragmentos = [FragmentoPersistido.model_validate(f) for f in datos["fragmentos"]]
        if (not fragmentos or any(f.documento_id != anterior.id for f in fragmentos)
                or len({f.id for f in fragmentos}) != len(fragmentos)
                or [f.orden for f in fragmentos] != list(range(1, len(fragmentos) + 1))):
            raise ValueError
        return anterior, fragmentos, datos["pfo_exigidas"]
    except (OSError, ValueError, TypeError, KeyError):
        raise ErrorReindexacion("respaldo_invalido") from None


def _reporte(documento, modo, cantidad, pfo):
    return {"estado": "validado", "modo": modo, "documento_id": documento.id,
            "drive_id": documento.drive_id, "titulo": documento.titulo,
            "metadata": {c: getattr(documento, c) for c in (
                "materia_id", "comision", "periodo", "tipo_documental", "estado", "vigente",
            )}, "fragmentos": cantidad, "pfo": pfo}


@asynccontextmanager
async def _concesion():
    propietario = str(uuid4())
    if not await asyncio.to_thread(repositorio.gestionar_bloqueo_sync, propietario, "adquirir"):
        raise ErrorReindexacion("bloqueo_ocupado")
    detener, perdido = asyncio.Event(), asyncio.Event()

    async def renovar():
        if perdido.is_set():
            raise ErrorReindexacion("bloqueo_perdido")
        try:
            valido = await asyncio.to_thread(repositorio.gestionar_bloqueo_sync, propietario, "renovar")
        except Exception:
            perdido.set()
            raise ErrorReindexacion("bloqueo_no_verificable") from None
        if not valido:
            perdido.set()
            raise ErrorReindexacion("bloqueo_perdido")

    async def latido():
        while not detener.is_set():
            try:
                await asyncio.wait_for(detener.wait(), timeout=INTERVALO_RENOVACION)
            except TimeoutError:
                try:
                    await renovar()
                except ErrorReindexacion:
                    return

    tarea = asyncio.create_task(latido())
    fallo = False
    try:
        yield propietario, renovar
    except BaseException:
        fallo = True
        raise
    finally:
        detener.set()
        await tarea
        try:
            liberado = await asyncio.to_thread(repositorio.gestionar_bloqueo_sync, propietario, "liberar")
        except Exception:
            liberado = False
        if not liberado:
            if not fallo:
                raise ErrorReindexacion("bloqueo_no_liberado_verificar_resultado")
            logger.warning("Reindexación: liberacion_bloqueo_fallida.")


async def reindexar_documento(
    *, drive_id, documento_id_esperado, titulo_esperado, pfo_exigidas=(),
    modo, respaldo=None, configuracion=None, gestor=None,
):
    """Dry-run/verificar nunca adquieren lease, crean proveedores o escriben."""
    if (not isinstance(drive_id, str) or not drive_id.strip()
            or type(documento_id_esperado) is not int or documento_id_esperado <= 0
            or not isinstance(titulo_esperado, str) or not titulo_esperado.strip()
            or modo not in {"dry-run", "aplicar", "verificar"}):
        raise ErrorReindexacion("argumentos_invalidos")
    pfo = list(pfo_exigidas)
    if (any(type(n) is not int or not 1 <= n <= 99 for n in pfo) or len(set(pfo)) != len(pfo)):
        raise ErrorReindexacion("requisitos_pfo_invalidos")
    config = configuracion if configuracion is not None else obtener_configuracion()
    if modo == "verificar":
        ruta = _ruta_respaldo(respaldo, leer=True)
        previo, _, requisitos = await asyncio.to_thread(_leer_respaldo, ruta)
        _validar_identidad(previo, drive_id, documento_id_esperado, titulo_esperado)
        if requisitos != pfo:
            raise ErrorReindexacion("requisitos_no_coinciden_con_respaldo")
        actual, _ = await _leer_objetivo(drive_id, documento_id_esperado, titulo_esperado, config)
        if actual.model_dump(exclude={"indexado_en"}) != previo.model_dump(exclude={"indexado_en"}):
            raise ErrorReindexacion("metadata_modificada")
        if actual.indexado_en == previo.indexado_en:
            raise ErrorReindexacion("indexacion_no_actualizada")
        fragmentos = await asyncio.to_thread(repositorio.obtener_fragmentos_documento, actual.id)
        if not fragmentos:
            raise ErrorReindexacion("fragmentos_ausentes")
        pfo_validas = validar_pfo_exigidas([f.contenido for f in fragmentos], pfo)
        final = await asyncio.to_thread(repositorio.obtener_documento_por_drive_id, drive_id)
        if final != actual:
            raise ErrorReindexacion("documento_cambio_durante_verificacion")
        return _reporte(actual, modo, len(fragmentos), pfo_validas) | {"dimension_vectores": 768}
    if modo == "dry-run":
        if respaldo is not None:
            raise ErrorReindexacion("respaldo_solo_para_aplicar_o_verificar")
        anterior, metadata = await _leer_objetivo(drive_id, documento_id_esperado, titulo_esperado, config)
        preparado = await _preparar(anterior, metadata)
        pfo_validas = validar_pfo_exigidas([c.page_content for c in preparado.chunks], pfo)
        return _reporte(anterior, modo, len(preparado.chunks), pfo_validas)

    ruta = _ruta_respaldo(respaldo)
    validar_configuracion_rag(config)
    propio = gestor is None
    central = gestor
    try:
        async with _concesion() as (propietario, renovar):
            anterior, metadata = await _leer_objetivo(drive_id, documento_id_esperado, titulo_esperado, config)
            fragmentos = await asyncio.to_thread(repositorio.obtener_fragmentos_documento, anterior.id)
            if not fragmentos:
                raise ErrorReindexacion("fragmentos_previos_ausentes")
            preparado = await _preparar(anterior, metadata)
            pfo_validas = validar_pfo_exigidas([c.page_content for c in preparado.chunks], pfo)
            await renovar()
            if central is None:
                central = obtener_gestor_embeddings()

            async def antes_de_persistir():
                await renovar()
                actual = await asyncio.to_thread(repositorio.obtener_documento_por_drive_id, drive_id)
                if actual != anterior:
                    raise ErrorReindexacion("documento_cambio_antes_de_escribir")
                actuales = await asyncio.to_thread(repositorio.obtener_fragmentos_documento, anterior.id)
                if actuales != fragmentos:
                    raise ErrorReindexacion("fragmentos_cambiaron_antes_de_escribir")
                await asyncio.to_thread(
                    sincronizador.validar_version_descargada, preparado.documento, preparado.contenido,
                )
                await asyncio.to_thread(_guardar_respaldo, ruta, anterior, fragmentos, pfo)
                await renovar()

            resultado = await sincronizador.indexar_documento_preparado(
                preparado, gestor=central, ejecucion_id=propietario, antes_de_persistir=antes_de_persistir,
            )
            if resultado.documento_id != anterior.id:
                raise ErrorReindexacion("identidad_resultado_invalida_verificar_commit")
            return _reporte(anterior, modo, resultado.fragmentos_indexados, pfo_validas) | {
                "estado": "reindexado", "indexado_en": resultado.indexado_en.isoformat(), "respaldo_creado": True,
            }
    finally:
        if propio and central is not None:
            for proveedor in central.proveedores:
                try:
                    await proveedor.aclose()
                except Exception:
                    logger.warning("Reindexación: cierre_proveedor_fallido.")
