"""Reindexación de un único DOCX con DB, Drive y embeddings simulados."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from docx import Document
from langchain_core.documents import Document as Chunk

from app.db.repositorio import ErrorRepositorio
from app.models.chat import MateriaChat
from app.models.rag import DocumentoPersistido, FragmentoPersistido, ResumenIndexacion
from app.providers.embeddings.base import ResultadoEmbeddings
from app.services import reindexacion as r, sincronizador as s
from app.settings import Configuracion

TITULO = "2026C2__Administracion-de-Base-de-Datos__COM-A__Cronograma.docx"
MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FECHA = datetime(2026, 11, 7, tzinfo=UTC)
ANTERIOR = datetime(2026, 11, 1)
NUEVA = datetime(2026, 11, 7)


def vector():
    return [1.] + [0.] * 767


def secciones():
    return [
        {"section": f"PFO {n}", "content": (
            "Materia: Administración de Base de Datos. Comisión: A. Período: 2026C2.\n"
            f"El período de apertura comienza el lunes {inicio} de septiembre de 2026 "
            f"y finaliza el domingo {fin} de septiembre de 2026."
        )} for n, inicio, fin in ((1, 1, 7), (2, 8, 14), (3, 15, 21), (4, 22, 28))
    ]


@pytest.fixture
def entorno(monkeypatch, tmp_path):
    archivo = Document()
    for seccion in secciones():
        archivo.add_heading(seccion["section"], level=1)
        archivo.add_paragraph(seccion["content"])
    memoria = BytesIO()
    archivo.save(memoria)
    contenido = memoria.getvalue()
    checksum = hashlib.md5(contenido, usedforsecurity=False).hexdigest()
    e = SimpleNamespace(
        documento=DocumentoPersistido(id=64, drive_id="drive-objetivo", titulo=TITULO,
            materia_id=2, comision="A", periodo="2026C2", tipo_documental="cronograma",
            estado="activo", vigente=True, indexado_en=ANTERIOR,
            drive_modified_time=FECHA, drive_md5_checksum=checksum),
        fragmentos=[FragmentoPersistido(id=307, documento_id=64,
            contenido="Texto anterior sin identificador de evento.", vector=vector(), orden=1)],
        otros={99: {"contenido": "Otro documento", "vector": vector(), "indexado_en": ANTERIOR}},
        ruta=tmp_path / "respaldo.json", fallo_rpc=False,
        config=Configuracion(_env_file=None, gemini_api_key="secreto-sintetico-no-volcar"),
    )
    e.get_documento = Mock(side_effect=lambda _: e.documento)
    e.get_fragmentos = Mock(side_effect=lambda identidad: [f.model_copy(deep=True) for f in e.fragmentos]
                           if identidad == 64 else pytest.fail("No leer otros documentos"))
    monkeypatch.setattr(r.repositorio, "obtener_documento_por_drive_id", e.get_documento)
    monkeypatch.setattr(r.repositorio, "obtener_fragmentos_documento", e.get_fragmentos)
    monkeypatch.setattr(r.repositorio, "listar_materias_chat", Mock(return_value=[
        MateriaChat(id=2, codigo="ABD", nombre="Administración de Base de Datos")]))
    e.bloqueo = Mock(return_value=True)
    monkeypatch.setattr(r.repositorio, "gestionar_bloqueo_sync", e.bloqueo)
    e.datos_drive = {"id": "drive-objetivo", "name": TITULO, "mimeType": MIME,
                     "modifiedTime": FECHA.isoformat(), "md5Checksum": checksum, "trashed": False}
    e.metadatos = Mock(side_effect=lambda identidad: dict(e.datos_drive)
                       if identidad == "drive-objetivo" else pytest.fail("No leer otro Drive ID"))
    monkeypatch.setattr(r, "obtener_metadatos_archivo", e.metadatos)
    e.descargar = Mock(return_value=contenido)
    monkeypatch.setattr(s, "descargar_contenido_archivo", e.descargar)
    e.validar_version = Mock()
    monkeypatch.setattr(s, "validar_version_descargada", e.validar_version)
    e.gestor = SimpleNamespace(
        embed_documents=AsyncMock(side_effect=lambda textos: ResultadoEmbeddings(
            [vector() for _ in textos], "gemini", "gemini-embedding-001", 768)),
        embed_query=AsyncMock(side_effect=AssertionError("No vectorizar consultas")), proveedores=(),
    )
    e.crear_gestor = Mock(side_effect=AssertionError("No crear proveedores en lectura"))
    monkeypatch.setattr(r, "obtener_gestor_embeddings", e.crear_gestor)

    def persistir(documento, fragmentos, *, metadata, ejecucion_id):
        assert documento.drive_id == "drive-objetivo" and ejecucion_id
        assert metadata.materia_id == 2 and metadata.comision == "A"
        if e.fallo_rpc:
            raise ErrorRepositorio("Fallo remoto simulado.")
        e.fragmentos = [FragmentoPersistido(id=1000 + f.orden, documento_id=64,
                         **f.model_dump()) for f in fragmentos]
        e.documento = e.documento.model_copy(update={"indexado_en": NUEVA})
        return ResumenIndexacion(documento_id=64, fragmentos_indexados=len(fragmentos), indexado_en=NUEVA)

    e.persistir = Mock(side_effect=persistir)
    monkeypatch.setattr(s, "sync_document_chunks", e.persistir)
    e.prohibidas = []
    for modulo, nombre in (
        (s, "_inactivar_invalido"), (s, "sincronizar_drive_completo"), (s, "regenerar_faq_despues_sync"),
        (r.repositorio, "actualizar_metadata_documento_sync"),
        (r.repositorio, "actualizar_estado_documento_sync"),
        (r.repositorio, "eliminar_fragmentos_documento"),
        (r.repositorio, "actualizar_documento_indexado"),
    ):
        prohibida = Mock(side_effect=AssertionError("No ejecutar operaciones ajenas"))
        monkeypatch.setattr(modulo, nombre, prohibida)
        e.prohibidas.append(prohibida)
    return e


def ejecutar(e, modo="dry-run", **cambios):
    opciones = dict(drive_id="drive-objetivo", documento_id_esperado=64,
                    titulo_esperado=TITULO, pfo_exigidas=[1, 2, 3, 4], modo=modo,
                    configuracion=e.config, gestor=e.gestor)
    if modo != "dry-run":
        opciones["respaldo"] = e.ruta
    return asyncio.run(r.reindexar_documento(**(opciones | cambios)))


def test_dry_run_conserva_pfo_en_chunks_sin_lock_embeddings_o_escrituras(entorno, capsys, caplog):
    e = entorno
    previo = e.documento.model_dump()
    resultado = ejecutar(e)
    assert resultado["modo"] == "dry-run" and resultado["estado"] == "validado"
    assert [f["identificador"] for f in resultado["pfo"]] == [f"PFO {n}" for n in range(1, 5)]
    assert all(f["ordenes"] == [n] for n, f in enumerate(resultado["pfo"], 1))
    assert resultado["fragmentos"] == 4 and e.documento.model_dump() == previo
    e.bloqueo.assert_not_called()
    e.gestor.embed_documents.assert_not_called()
    e.crear_gestor.assert_not_called()
    e.persistir.assert_not_called()
    assert not e.ruta.exists()
    for operacion in e.prohibidas:
        operacion.assert_not_called()
    salida = json.dumps(resultado) + capsys.readouterr().out + caplog.text
    assert "período de apertura" not in salida and "secreto-sintetico" not in salida


def test_aplicar_md5_identico_usa_preparacion_comun_una_rpc_y_respaldo(entorno):
    e = entorno
    previo = e.documento.model_dump()
    assert e.datos_drive["md5Checksum"] == e.documento.drive_md5_checksum
    resultado = ejecutar(e, "aplicar")
    assert resultado["estado"] == "reindexado" and resultado["documento_id"] == 64
    assert e.documento.model_dump(exclude={"indexado_en"}) == {k: v for k, v in previo.items() if k != "indexado_en"}
    e.gestor.embed_documents.assert_awaited_once()
    e.persistir.assert_called_once()
    assert len(e.fragmentos) == 4 and all(f.contenido.startswith(f"PFO {f.orden}\n") for f in e.fragmentos)
    assert e.otros[99]["contenido"] == "Otro documento" and e.otros[99]["vector"] == vector()
    assert e.validar_version.call_count == 2
    respaldo = json.loads(e.ruta.read_text(encoding="utf-8"))
    assert respaldo["documento"]["id"] == 64 and len(respaldo["fragmentos"]) == 1
    assert respaldo["fragmentos"][0]["id"] == 307
    assert respaldo["fragmentos"][0]["vector"] == vector()
    assert "secreto-sintetico" not in e.ruta.read_text()
    assert e.bloqueo.call_args_list[0].args[1] == "adquirir"
    assert e.bloqueo.call_args_list[-1].args[1] == "liberar"
    assert len({c.args[0] for c in e.bloqueo.call_args_list}) == 1
    for operacion in e.prohibidas:
        operacion.assert_not_called()


@pytest.mark.parametrize("cambio,categoria", [
    ({"documento_id_esperado": 65}, "identidad_no_coincide"),
    ({"titulo_esperado": "Otro.docx"}, "titulo_no_coincide"),
])
def test_identidad_incorrecta_aborta_antes_de_drive(entorno, cambio, categoria):
    with pytest.raises(r.ErrorReindexacion, match=categoria):
        ejecutar(entorno, **cambio)
    entorno.metadatos.assert_not_called()
    entorno.descargar.assert_not_called()
    entorno.persistir.assert_not_called()


@pytest.mark.parametrize("cambio,categoria", [
    ({"estado": "inactivo"}, "documento_no_activo_vigente"),
    ({"vigente": False}, "documento_no_activo_vigente"),
    ({"vigente": None}, "documento_no_activo_vigente"),
    ({"indexado_en": None}, "documento_no_indexado"),
    ({"materia_id": 99}, "metadata_no_coincide"),
    ({"comision": "B"}, "metadata_no_coincide"),
    ({"periodo": "2027C1"}, "metadata_no_coincide"),
    ({"tipo_documental": "programa"}, "metadata_no_coincide"),
])
def test_objetivo_incompatible_no_se_corrige_ni_reactiva(entorno, cambio, categoria):
    entorno.documento = entorno.documento.model_copy(update=cambio)
    with pytest.raises(r.ErrorReindexacion, match=categoria):
        ejecutar(entorno)
    entorno.descargar.assert_not_called()
    entorno.persistir.assert_not_called()


@pytest.mark.parametrize("campo,valor", [("id", "otro"), ("name", "Otro.docx"),
    ("trashed", True), ("modifiedTime", "2026-11-08T00:00:00+00:00"), ("md5Checksum", "b" * 32)])
def test_drive_no_coincidente_no_se_descarga(entorno, campo, valor):
    entorno.datos_drive[campo] = valor
    with pytest.raises(r.ErrorReindexacion):
        ejecutar(entorno)
    entorno.descargar.assert_not_called()
    entorno.persistir.assert_not_called()


def test_pfo_requerida_ausente_no_vectoriza_ni_escribe(entorno, monkeypatch):
    monkeypatch.setattr(s, "extraer_texto_desde_bytes", Mock(return_value=secciones()[:3]))
    with pytest.raises(r.ErrorReindexacion, match="pfo_requerida"):
        ejecutar(entorno, "aplicar")
    entorno.gestor.embed_documents.assert_not_called()
    entorno.persistir.assert_not_called()
    assert not entorno.ruta.exists()


@pytest.mark.parametrize("contenidos", [
    ["PFO 2"],
    ["PFO 2", "Fecha: del 8 al 14 de septiembre de 2026."],
    ["PFO 1: del 8 al 14 de septiembre de 2026."],
    ["PFO 2: 8 de septiembre de 2026."],
    ["PFO 2: del 8 al 14 de septiembre de 2026.", "PFO 2: del 15 al 21 de septiembre de 2026."],
])
def test_exigir_pfo_no_infiere_identidad_rango_o_adyacencia(contenidos):
    with pytest.raises(r.ErrorReindexacion):
        r.validar_pfo_exigidas(contenidos, [2])


def test_lock_ocupado_no_descarga_ni_vectoriza(entorno):
    entorno.bloqueo.return_value = False
    with pytest.raises(r.ErrorReindexacion, match="bloqueo_ocupado"):
        ejecutar(entorno, "aplicar")
    entorno.descargar.assert_not_called()
    entorno.persistir.assert_not_called()
    assert [c.args[1] for c in entorno.bloqueo.call_args_list] == ["adquirir"]


@pytest.mark.parametrize("despues_embeddings", [False, True])
def test_lock_perdido_aborta_antes_de_rpc(entorno, despues_embeddings):
    e = entorno
    perdido = not despues_embeddings
    e.bloqueo.side_effect = lambda propietario, accion: not (perdido and accion == "renovar")
    if despues_embeddings:
        async def generar(textos):
            nonlocal perdido
            perdido = True
            return ResultadoEmbeddings([vector() for _ in textos], "gemini", "gemini-embedding-001", 768)
        e.gestor.embed_documents.side_effect = generar
    with pytest.raises(r.ErrorReindexacion, match="bloqueo_perdido"):
        ejecutar(e, "aplicar")
    e.persistir.assert_not_called()
    assert not e.ruta.exists()
    assert e.bloqueo.call_args_list[-1].args[1] == "liberar"


def test_error_embeddings_no_modifica_version_ni_genera_respaldo(entorno):
    previo = entorno.documento.model_dump()
    entorno.gestor.embed_documents.side_effect = RuntimeError("payload que no debe volcarse")
    with pytest.raises(s.ErrorPreparacionIndexacion, match="embeddings_fallidos"):
        ejecutar(entorno, "aplicar")
    entorno.persistir.assert_not_called()
    assert entorno.documento.model_dump() == previo and not entorno.ruta.exists()


def test_embedding_incompatible_no_llega_al_callback_ni_rpc(entorno):
    from app.services.validacion_embeddings_rag import ErrorEmbeddingIncompatible
    entorno.gestor.embed_documents.side_effect = None
    entorno.gestor.embed_documents.return_value = ResultadoEmbeddings([[1.] * 1024] * 4, "cloudflare", "otro", 1024)
    with pytest.raises(ErrorEmbeddingIncompatible):
        ejecutar(entorno, "aplicar")
    assert not entorno.ruta.exists()
    entorno.persistir.assert_not_called()


def test_error_rpc_usa_reemplazo_unico_y_conserva_respaldo_para_verificar(entorno):
    e = entorno
    previo = e.documento.model_dump()
    originales = [f.model_dump() for f in e.fragmentos]
    e.fallo_rpc = True
    with pytest.raises(s.ErrorPreparacionIndexacion, match="persistencia_fallida"):
        ejecutar(e, "aplicar")
    e.persistir.assert_called_once()
    assert e.documento.model_dump() == previo and [f.model_dump() for f in e.fragmentos] == originales
    assert e.ruta.exists()
    # El doble conserva la versión; el rollback PostgreSQL real es contrato de la RPC,
    # no una garantía demostrada por este mock.
    for operacion in e.prohibidas:
        operacion.assert_not_called()


@pytest.mark.parametrize("cambio", ["documento", "fragmentos", "drive"])
def test_cambio_durante_embeddings_aborta_antes_de_escribir(entorno, cambio):
    e = entorno
    async def generar(textos):
        if cambio == "documento":
            e.documento = e.documento.model_copy(update={"indexado_en": NUEVA})
        elif cambio == "fragmentos":
            e.fragmentos = [e.fragmentos[0].model_copy(update={"contenido": "Otro contenido"})]
        else:
            e.validar_version.side_effect = ValueError("cambió Drive")
        return ResultadoEmbeddings([vector() for _ in textos], "gemini", "gemini-embedding-001", 768)
    e.gestor.embed_documents.side_effect = generar
    with pytest.raises((r.ErrorReindexacion, ValueError)):
        ejecutar(e, "aplicar")
    e.persistir.assert_not_called()
    assert not e.ruta.exists()


def test_respaldo_dentro_repo_no_adquiere_lock(entorno):
    with pytest.raises(r.ErrorReindexacion, match="respaldo_dentro"):
        ejecutar(entorno, "aplicar", respaldo=r.RAIZ_REPOSITORIO / "no-crear.json")
    entorno.bloqueo.assert_not_called()
    entorno.persistir.assert_not_called()


def test_respaldo_existente_no_se_sobrescribe(entorno):
    entorno.ruta.write_text("conservar")
    with pytest.raises(r.ErrorReindexacion, match="ruta_respaldo"):
        ejecutar(entorno, "aplicar")
    assert entorno.ruta.read_text() == "conservar"
    entorno.bloqueo.assert_not_called()


def test_error_al_guardar_respaldo_no_llega_a_rpc(entorno, monkeypatch):
    monkeypatch.setattr(r, "_guardar_respaldo", Mock(side_effect=OSError("sin espacio")))
    with pytest.raises(OSError):
        ejecutar(entorno, "aplicar")
    entorno.persistir.assert_not_called()


def test_verificar_solo_lee_y_compara_metadata_vectores_y_pfo(entorno):
    e = entorno
    ejecutar(e, "aplicar")
    for operacion in (e.bloqueo, e.persistir, e.metadatos, e.descargar, e.gestor.embed_documents, e.crear_gestor):
        operacion.reset_mock()
    resultado = ejecutar(e, "verificar")
    assert resultado["modo"] == "verificar" and resultado["dimension_vectores"] == 768
    assert resultado["fragmentos"] == 4 and len(resultado["pfo"]) == 4
    for operacion in (e.bloqueo, e.persistir, e.metadatos, e.descargar, e.gestor.embed_documents, e.crear_gestor):
        operacion.assert_not_called()


@pytest.mark.parametrize("cambio", ["metadata", "indexado_en", "sin_fragmentos", "sin_pfo", "cambio_durante_lectura"])
def test_verificacion_rechaza_resultados_incompletos_o_inestables(entorno, cambio):
    e = entorno
    ejecutar(e, "aplicar")
    if cambio == "metadata":
        e.documento = e.documento.model_copy(update={"drive_md5_checksum": "c" * 32})
    elif cambio == "indexado_en":
        e.documento = e.documento.model_copy(update={"indexado_en": ANTERIOR})
    elif cambio == "sin_fragmentos":
        e.fragmentos = []
    elif cambio == "sin_pfo":
        e.fragmentos = [e.fragmentos[0]]
    else:
        final = e.documento.model_copy(update={"indexado_en": datetime(2026, 11, 8)})
        e.get_documento.side_effect = [e.documento, final]
    with pytest.raises(r.ErrorReindexacion):
        ejecutar(e, "verificar")


def test_cierre_fallido_no_oculta_commit_confirmado(entorno):
    e = entorno
    e.gestor.proveedores = (SimpleNamespace(aclose=AsyncMock(side_effect=RuntimeError("secreto"))),)
    e.crear_gestor.side_effect = None
    e.crear_gestor.return_value = e.gestor
    resultado = ejecutar(e, "aplicar", gestor=None)
    assert resultado["estado"] == "reindexado"
    e.persistir.assert_called_once()


def test_sync_normal_sigue_omitiendo_md5_identico(entorno, monkeypatch):
    e = entorno
    datos = e.datos_drive
    documento = s.DocumentoDrive(drive_id=datos["id"], titulo=datos["name"], tipo_mime=datos["mimeType"],
                                 drive_modified_time=FECHA, drive_md5_checksum=datos["md5Checksum"])
    monkeypatch.setattr(s, "obtener_documento_por_drive_id", e.get_documento)
    monkeypatch.setattr(s.repositorio, "contar_fragmentos_documento", Mock(return_value=1))
    resultado = asyncio.run(s.run_full_sync([documento], gestor=e.gestor, configuracion=e.config))
    assert resultado.errores == 0 and resultado.resultados[0].resumen.indexado_en == ANTERIOR
    e.descargar.assert_not_called()
    e.persistir.assert_not_called()
    e.gestor.embed_documents.assert_not_called()
