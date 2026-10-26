"""Reconciliación con dobles de Drive/DB: no accede a servicios reales."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.models.rag import (
    DocumentoDrive, DocumentoPersistido, FragmentoRecuperado, ResultadoIndexacionDocumento,
    ResultadoSincronizacion, ResumenIndexacion,
)
from app.services import sincronizador as s
from app.services import recuperador
from app.providers.embeddings.base import ResultadoEmbedding
from app.settings import Configuracion

FECHA = datetime(2026, 10, 23, 12, tzinfo=UTC)


def drive(identidad="uno", **cambios):
    return DocumentoDrive(**dict({
        "drive_id": identidad, "titulo": "GENERAL__Mismo-nombre.pdf", "tipo_mime": "application/pdf",
        "drive_modified_time": FECHA, "drive_md5_checksum": "a" * 32,
    }, **cambios))


def persistido(identidad="uno", **cambios):
    return DocumentoPersistido(**dict({
        "id": 1, "drive_id": identidad, "titulo": "GENERAL__Mismo-nombre.pdf", "estado": "activo",
        "vigente": True, "indexado_en": FECHA, "drive_modified_time": FECHA,
        "drive_md5_checksum": "a" * 32,
    }, **cambios))


@pytest.fixture
def mundo(monkeypatch):
    almacen = {}
    archivos = []
    listar = Mock(side_effect=lambda: list(archivos))
    leer = Mock(side_effect=lambda: list(almacen.values()))
    bloqueo = Mock(return_value=True)
    catalogo = Mock(return_value=[])
    monkeypatch.setattr(s.repositorio, "listar_materias_chat", catalogo)

    async def indexar(documentos, **kwargs):
        assert kwargs["ejecucion_id"]
        documento = documentos[0]
        previo = almacen.get(documento.drive_id)
        identidad = previo.id if previo else max((d.id for d in almacen.values()), default=0) + 1
        almacen[documento.drive_id] = persistido(
            documento.drive_id, id=identidad, titulo=documento.titulo,
            drive_modified_time=documento.drive_modified_time,
            drive_md5_checksum=documento.drive_md5_checksum,
        )
        return ResultadoSincronizacion(resultados=[ResultadoIndexacionDocumento(
            drive_id=documento.drive_id, titulo=documento.titulo,
            resumen=ResumenIndexacion(documento_id=identidad, fragmentos_indexados=1,
                                      indexado_en=FECHA),
        )])

    def actualizar(drive_id, activo, propietario, *, titulo=None):
        documento = almacen[drive_id]
        almacen[drive_id] = documento.model_copy(update={
            "estado": "activo" if activo else "inactivo", "vigente": activo,
            "titulo": titulo or documento.titulo,
        })
        return True

    def actualizar_metadata(documento, metadata, propietario):
        previo = almacen[documento.drive_id]
        almacen[documento.drive_id] = previo.model_copy(update={
            "titulo": documento.titulo, "materia_id": metadata.materia_id,
            "comision": metadata.comision, "periodo": metadata.periodo,
            "drive_modified_time": documento.drive_modified_time,
            "drive_md5_checksum": documento.drive_md5_checksum, "estado": "activo", "vigente": True,
        })
        return True

    indexacion = AsyncMock(side_effect=indexar)
    estado = Mock(side_effect=actualizar)
    metadata = Mock(side_effect=actualizar_metadata)
    monkeypatch.setattr(s.repositorio, "actualizar_metadata_documento_sync", metadata)
    monkeypatch.setattr(s, "listar_documentos_sincronizables", listar)
    monkeypatch.setattr(s.repositorio, "listar_documentos_sync", leer)
    monkeypatch.setattr(s.repositorio, "gestionar_bloqueo_sync", bloqueo)
    monkeypatch.setattr(s.repositorio, "actualizar_estado_documento_sync", estado)
    monkeypatch.setattr(s, "run_full_sync", indexacion)
    return SimpleNamespace(almacen=almacen, archivos=archivos, indexacion=indexacion,
                           estado=estado, listar=listar, leer=leer, bloqueo=bloqueo,
                           metadata=metadata, catalogo=catalogo)


def ejecutar():
    return asyncio.run(s.sincronizar_drive_completo(configuracion=Configuracion(), gestor=object()))


def test_nuevo_y_repeticion_idempotente(mundo):
    mundo.archivos.append(drive())
    assert ejecutar().nuevos == 1
    original = mundo.almacen["uno"].id
    resultado = ejecutar()
    assert resultado.nuevos == resultado.actualizados == resultado.inactivados == 0
    assert resultado.sin_cambios == 1
    assert mundo.almacen["uno"].id == original
    mundo.indexacion.assert_awaited_once()
    mundo.estado.assert_not_called()


def test_sin_cambios_no_indexa_ni_escribe_documentos(mundo):
    mundo.archivos.append(drive())
    mundo.almacen["uno"] = persistido()
    assert ejecutar().sin_cambios == 1
    mundo.indexacion.assert_not_called()
    mundo.estado.assert_not_called()


@pytest.mark.parametrize("cambios", [
    {"drive_md5_checksum": "b" * 32},
])
def test_modificado_conserva_identidad(mundo, cambios):
    mundo.almacen["uno"] = persistido(id=7)
    mundo.archivos.append(drive(**cambios))
    assert ejecutar().actualizados == 1
    assert mundo.almacen["uno"].id == 7


def test_baseline_sin_version_reindexa_una_sola_vez(mundo):
    mundo.almacen["uno"] = persistido(drive_modified_time=None, drive_md5_checksum=None)
    mundo.archivos.append(drive())
    assert ejecutar().actualizados == 1
    assert ejecutar().sin_cambios == 1
    mundo.indexacion.assert_awaited_once()


def test_google_docs_sin_checksum(mundo):
    mundo.almacen["uno"] = persistido(drive_md5_checksum=None)
    mundo.archivos.append(drive(tipo_mime="application/vnd.google-apps.document",
                                drive_md5_checksum=None))
    assert ejecutar().sin_cambios == 1
    mundo.indexacion.assert_not_called()


def test_baja_logica_lista_vacia_y_repeticion(mundo):
    mundo.almacen["uno"] = persistido(id=7)
    assert ejecutar().inactivados == 1
    assert mundo.almacen["uno"].id == 7
    assert mundo.almacen["uno"].estado == "inactivo"
    assert mundo.almacen["uno"].vigente is False
    assert ejecutar().inactivados == 0
    mundo.estado.assert_called_once()
    mundo.indexacion.assert_not_called()


def test_mismo_nombre_distinto_drive_id_es_otro_documento(mundo):
    mundo.almacen["uno"] = persistido(id=7)
    mundo.archivos.append(drive("dos"))
    resultado = ejecutar()
    assert resultado.nuevos == resultado.inactivados == 1
    assert mundo.almacen["dos"].id != 7
    assert mundo.almacen["uno"].estado == "inactivo"


@pytest.mark.parametrize("modificado", [False, True])
def test_reaparicion(mundo, modificado):
    mundo.almacen["uno"] = persistido(estado="inactivo", vigente=False)
    mundo.archivos.append(drive(drive_md5_checksum=("b" if modificado else "a") * 32))
    resultado = ejecutar()
    assert resultado.reactivados == 1
    assert resultado.actualizados == int(modificado)
    assert mundo.indexacion.await_count == int(modificado)
    assert mundo.almacen["uno"].estado == "activo"


def test_renombrado_no_genera_embeddings(mundo):
    mundo.almacen["uno"] = persistido()
    mundo.archivos.append(drive(titulo="GENERAL__Nombre-actualizado.pdf"))
    assert ejecutar().actualizados == 1
    assert mundo.almacen["uno"].titulo == "GENERAL__Nombre-actualizado.pdf"
    mundo.indexacion.assert_not_called()


def test_md5_igual_prioritario_actualiza_version_sin_indexar(mundo):
    mundo.almacen["uno"] = persistido(id=7)
    mundo.archivos.append(drive(drive_modified_time=FECHA + timedelta(days=1)))
    assert ejecutar().actualizados == 1
    assert mundo.almacen["uno"].drive_modified_time == FECHA + timedelta(days=1)
    assert mundo.almacen["uno"].indexado_en == FECHA
    assert ejecutar().sin_cambios == 1
    mundo.indexacion.assert_not_called()


@pytest.mark.parametrize("tipo,checksum", [
    ("application/vnd.google-apps.document", "a" * 32),
    ("application/pdf", None),
    ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", None),
])
def test_sin_checksum_o_docs_usa_modifiedtime(mundo, tipo, checksum):
    mundo.almacen["uno"] = persistido(id=7)
    mundo.archivos.append(drive(tipo_mime=tipo, drive_md5_checksum=checksum,
                                drive_modified_time=FECHA + timedelta(seconds=1)))
    assert ejecutar().actualizados == 1
    mundo.indexacion.assert_awaited_once()


@pytest.mark.parametrize("nuevo_titulo,esperado", [
    ("2026C2__Tecnicas-de-Programacion__COM-GENERAL__Programa.pdf", (47, None, "2026C2")),
    ("2026C2__Tecnicas-de-Programacion__COM-F__Cronograma.pdf", (47, "F", "2026C2")),
    ("GENERAL__Ingreso.pdf", (None, None, None)),
])
def test_cambio_materia_y_general_limpia_metadata_sin_embeddings(mundo, nuevo_titulo, esperado):
    mundo.catalogo.return_value = [SimpleNamespace(id=47, nombre="Técnicas de Programación")]
    mundo.almacen["uno"] = persistido(id=7, materia_id=81, comision="A", periodo="2026C1")
    mundo.archivos.append(drive(titulo=nuevo_titulo, drive_modified_time=FECHA + timedelta(minutes=1)))
    assert ejecutar().actualizados == 1
    guardado = mundo.almacen["uno"]
    assert (guardado.materia_id, guardado.comision, guardado.periodo) == esperado
    assert guardado.id == 7 and guardado.indexado_en == FECHA
    mundo.indexacion.assert_not_called()
    mundo.catalogo.assert_called_once()


def test_nombre_invalido_inactiva_conserva_metadata_y_corrige_sin_reindexar(mundo):
    previo = persistido(id=7, materia_id=47, comision="F", periodo="2026C2")
    mundo.almacen["uno"] = previo
    mundo.archivos.append(drive(titulo="2026C2__Inexistente__COM-F__Programa.pdf"))
    resultado = ejecutar()
    assert resultado.inactivados == 1 and resultado.errores == 1
    assert resultado.detalles[0].categoria == "materia_no_reconocida"
    inactivo = mundo.almacen["uno"]
    assert inactivo.titulo == previo.titulo and inactivo.materia_id == 47
    assert inactivo.comision == "F" and inactivo.periodo == "2026C2"
    assert inactivo.indexado_en == FECHA and inactivo.id == 7
    assert inactivo.estado == "inactivo" and inactivo.vigente is False
    assert ejecutar().inactivados == 0
    mundo.estado.assert_called_once()  # No se vuelve a tratar como ausencia.
    mundo.archivos[0] = drive(titulo="GENERAL__Corregido.pdf")
    assert ejecutar().reactivados == 1
    assert mundo.almacen["uno"].materia_id is None
    mundo.indexacion.assert_not_called()


def test_nuevo_invalido_no_indexa_ni_crea(mundo):
    mundo.archivos.append(drive(titulo="2026C3__Materia__COM-A__Programa.pdf"))
    resultado = ejecutar()
    assert resultado.errores == 1 and resultado.nuevos == 0 and not mundo.almacen
    mundo.indexacion.assert_not_called()
    mundo.metadata.assert_not_called()


def test_catalogo_caido_aborta_antes_de_cualquier_cambio(mundo):
    mundo.almacen["uno"] = persistido()
    mundo.catalogo.side_effect = RuntimeError("SECRETO")
    resultado = ejecutar()
    assert resultado.estado == "error" and resultado.detalles[0].categoria == "catalogo_materias_fallido"
    mundo.estado.assert_not_called()
    mundo.metadata.assert_not_called()
    mundo.indexacion.assert_not_called()


@pytest.mark.parametrize("fallo", ["listar", "leer"])
def test_error_global_no_produce_bajas(mundo, fallo):
    mundo.almacen["uno"] = persistido()
    getattr(mundo, fallo).side_effect = RuntimeError("SECRETO")
    resultado = ejecutar()
    assert resultado.estado == "error"
    assert "SECRETO" not in resultado.model_dump_json()
    mundo.estado.assert_not_called()
    mundo.indexacion.assert_not_called()
    assert mundo.bloqueo.call_args.args[1] == "liberar"


def test_falla_documento_continua_y_no_pierde_version(mundo):
    mundo.almacen["uno"] = persistido(drive_modified_time=None)
    mundo.archivos.extend([drive(), drive("dos")])
    bueno = mundo.indexacion.side_effect

    async def mixto(documentos, **kwargs):
        if documentos[0].drive_id == "uno":
            return ResultadoSincronizacion(resultados=[ResultadoIndexacionDocumento(
                drive_id="uno", titulo="Mismo nombre.pdf", error="extraccion_fallida",
            )])
        return await bueno(documentos, **kwargs)

    mundo.indexacion.side_effect = mixto
    resultado = ejecutar()
    assert resultado.estado == "parcial" and resultado.errores == 1
    assert resultado.nuevos == 1
    assert mundo.almacen["uno"].drive_modified_time is None


def test_bloqueo_ocupado_no_accede_drive(mundo):
    mundo.bloqueo.return_value = False
    with pytest.raises(s.SincronizacionEnCurso):
        ejecutar()
    mundo.listar.assert_not_called()
    assert mundo.bloqueo.call_count == 1


def test_perdida_bloqueo_detiene_escrituras(mundo):
    mundo.almacen["uno"] = persistido()
    mundo.bloqueo.side_effect = lambda propietario, accion: accion != "renovar"
    assert ejecutar().estado == "error"
    mundo.estado.assert_not_called()
    mundo.indexacion.assert_not_called()


def test_snapshot_invalido_no_inactiva(mundo):
    mundo.almacen["uno"] = persistido()
    mundo.archivos.extend([drive(), drive()])
    assert ejecutar().estado == "error"
    mundo.estado.assert_not_called()
    mundo.indexacion.assert_not_called()


def test_baja_se_refleja_en_recuperador_sin_cachear_fragmentos(mundo, monkeypatch):
    mundo.almacen["uno"] = persistido(id=7)
    # El doble modela el contrato de match_fragmentos. El SQL manual comprueba
    # separadamente el filtro real de PostgreSQL después de aplicar 0003.
    def buscar(vector, *, filtros, documento_ids, top_k):
        assert filtros.vigente is True and documento_ids == [7]
        documento = mundo.almacen["uno"]
        if documento.estado != "activo" or documento.vigente is not True:
            return []
        return [FragmentoRecuperado(
            fragmento_id=77, documento_id=7, contenido="DELTA2026", orden=1, similitud=0.9,
            titulo=documento.titulo, drive_id="uno", materia_id=None, comision=None,
            periodo=None, vigente=True,
        )]

    monkeypatch.setattr(recuperador.repositorio, "buscar_fragmentos_similares", buscar)
    gestor = SimpleNamespace(embed_query=AsyncMock(return_value=ResultadoEmbedding(
        [1.0] + [0.0] * 767, "gemini", "gemini-embedding-001", 768)))

    async def consulta():
        return await recuperador.recuperar_fragmentos(
            "DELTA2026", gestor=gestor, configuracion=Configuracion(), documento_ids=[7],
        )

    assert asyncio.run(consulta()).evidencia_suficiente
    assert ejecutar().inactivados == 1
    assert asyncio.run(consulta()).fragmentos == []
