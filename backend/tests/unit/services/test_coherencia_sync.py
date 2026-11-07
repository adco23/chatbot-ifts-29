"""La validación precede TODAS las escrituras sobre una versión documental."""

import asyncio
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from docx import Document

from app.drive.extractores import extraer_texto_desde_bytes
from app.models.chat import MateriaChat
from app.models.rag import DocumentoDrive, DocumentoPersistido, ResumenIndexacion
from app.providers.embeddings.base import ResultadoEmbeddings
from app.services import sincronizador as s
from app.settings import Configuracion

FECHA = datetime(2027, 1, 1, tzinfo=UTC)
TITULO = "2027C1__Tecnicas-de-Programacion__COM-A__Cronograma.pdf"


@pytest.fixture
def mundo(monkeypatch):
    e = SimpleNamespace(
        documento=DocumentoDrive(drive_id="original", titulo=TITULO, tipo_mime="application/pdf",
                                  drive_modified_time=FECHA, drive_md5_checksum="b" * 32),
        previo=DocumentoPersistido(id=91, drive_id="original", titulo=TITULO, materia_id=19,
                                    comision="A", periodo="2027C1", tipo_documental="cronograma",
                                    vigente=True, estado="activo", indexado_en=FECHA,
                                    drive_modified_time=FECHA, drive_md5_checksum="a" * 32),
        cuerpo="Comisión F\nSegundo cuatrimestre de 2026\nInformación reservada.",
    )
    e.config = Configuracion(_env_file=None, faq_regeneracion_maximo=0)
    e.catalogo = [MateriaChat(id=19, codigo="TP", nombre="Técnicas de Programación")]
    monkeypatch.setattr(s.repositorio, "listar_materias_chat", lambda: e.catalogo)
    monkeypatch.setattr(s, "obtener_documento_por_drive_id", lambda _: e.previo)
    monkeypatch.setattr(s.repositorio, "listar_documentos_sync", lambda: [e.previo] if e.previo else [])
    monkeypatch.setattr(s, "listar_documentos_sincronizables", lambda: [e.documento])
    monkeypatch.setattr(s.repositorio, "gestionar_bloqueo_sync", Mock(return_value=True))
    e.descargar = Mock(return_value=b"bytes")
    e.extraer = Mock(side_effect=lambda *a, **k: [{"section": "General", "content": e.cuerpo}])
    e.leer = Mock(side_effect=lambda *a, **k: {91: e.cuerpo})
    e.metadata = Mock(return_value=True)
    e.estado = Mock(return_value=True)
    e.persistir = Mock(return_value=ResumenIndexacion(documento_id=91, fragmentos_indexados=1,
                                                     indexado_en=FECHA))
    e.gestor = SimpleNamespace(embed_documents=AsyncMock(side_effect=lambda textos: ResultadoEmbeddings(
        [[1.] + [0.] * 767 for _ in textos], "gemini", "gemini-embedding-001", 768)))
    monkeypatch.setattr(s, "descargar_contenido_archivo", e.descargar)
    monkeypatch.setattr(s, "extraer_texto_desde_bytes", e.extraer)
    monkeypatch.setattr(s, "validar_version_descargada", Mock())
    monkeypatch.setattr(s.repositorio, "obtener_contenidos_documentos", e.leer)
    monkeypatch.setattr(s.repositorio, "actualizar_metadata_documento_sync", e.metadata)
    monkeypatch.setattr(s.repositorio, "actualizar_estado_documento_sync", e.estado)
    monkeypatch.setattr(s.repositorio, "contar_fragmentos_documento", Mock(return_value=1))
    monkeypatch.setattr(s, "sync_document_chunks", e.persistir)
    return e


def ejecutar(e, modo):
    if modo == "explicita":
        return asyncio.run(s.run_full_sync([e.documento], gestor=e.gestor, configuracion=e.config))
    return asyncio.run(s.sincronizar_drive_completo(gestor=e.gestor, configuracion=e.config))


@pytest.mark.parametrize("modo", ["explicita", "completa"])
@pytest.mark.parametrize("camino", ["nuevo", "contenido", "metadata", "reactivacion"])
@pytest.mark.parametrize("vigente", [True, False, None])
def test_rechazo_preserva_version_completa_sin_embeddings(mundo, modo, camino, vigente):
    e = mundo
    e.previo = e.previo.model_copy(update={"vigente": vigente})
    if camino == "nuevo":
        e.previo = None
    elif camino in ("metadata", "reactivacion"):
        e.documento = e.documento.model_copy(update={"drive_md5_checksum": "a" * 32})
        cambios = {"titulo": "Titulo previo"} if camino == "metadata" else {"estado": "inactivo"}
        e.previo = e.previo.model_copy(update=cambios)
    previo = e.previo.model_dump() if e.previo else None
    resultado = ejecutar(e, modo)
    assert resultado.errores == 1
    assert (resultado.resultados[0].error if modo == "explicita" else
            resultado.detalles[0].categoria) == "incoherencia_documental"
    assert (e.previo.model_dump() if e.previo else None) == previo
    e.metadata.assert_not_called()
    e.estado.assert_not_called()
    e.persistir.assert_not_called()
    e.gestor.embed_documents.assert_not_called()
    if camino in ("metadata", "reactivacion"):
        e.descargar.assert_not_called()
        e.extraer.assert_not_called()
        e.leer.assert_called_once_with([91], preservar_estructura=True)


@pytest.mark.parametrize("modo", ["explicita", "completa"])
def test_compatible_indexa_por_recorrido_existente(mundo, modo):
    mundo.cuerpo = "Comisión A\nPeríodo: 2027-1C\nLas clases siguen este cronograma."
    resultado = ejecutar(mundo, modo)
    assert resultado.errores == 0
    mundo.gestor.embed_documents.assert_awaited_once()
    mundo.persistir.assert_called_once()
    assert "Período: 2027-1C" in mundo.persistir.call_args.args[1][0].contenido


@pytest.mark.parametrize("modo", ["explicita", "completa"])
def test_metadata_compatible_actualiza_sin_descarga_o_embeddings(mundo, modo):
    mundo.cuerpo = "Comisión A\nPeríodo: 2027-1C\nContenido previo."
    mundo.documento = mundo.documento.model_copy(update={"drive_md5_checksum": "a" * 32})
    mundo.previo = mundo.previo.model_copy(update={"titulo": "Antes"})
    assert ejecutar(mundo, modo).errores == 0
    mundo.metadata.assert_called_once()
    mundo.descargar.assert_not_called()
    mundo.gestor.embed_documents.assert_not_called()
    mundo.persistir.assert_not_called()


@pytest.mark.parametrize("modo", ["explicita", "completa"])
def test_sin_cambios_no_revisa_ni_reindexa(mundo, modo):
    mundo.documento = mundo.documento.model_copy(update={"drive_md5_checksum": "a" * 32})
    assert ejecutar(mundo, modo).errores == 0
    mundo.leer.assert_not_called()
    mundo.metadata.assert_not_called()
    mundo.descargar.assert_not_called()
    mundo.gestor.embed_documents.assert_not_called()


@pytest.mark.parametrize("modo", ["explicita", "completa"])
def test_metadata_sin_texto_no_escribe(mundo, modo, caplog):
    mundo.documento = mundo.documento.model_copy(update={"drive_md5_checksum": "a" * 32})
    mundo.previo = mundo.previo.model_copy(update={"titulo": "Antes"})
    mundo.leer.side_effect = None
    mundo.leer.return_value = {}
    assert ejecutar(mundo, modo).errores == 1
    mundo.metadata.assert_not_called()
    assert any(getattr(r, "coherencia_documental", {}).get("categoria") == "contenido_no_disponible"
               for r in caplog.records)


def test_rechazo_continua_con_otro_documento_sin_registrar_contenido(mundo, monkeypatch, caplog):
    otro = mundo.documento.model_copy(update={"drive_id": "otro"})
    mundo.extraer.side_effect = [
        [{"section": "General", "content": mundo.cuerpo}],
        [{"section": "General", "content": "Comisión A\nPeríodo: 2027C1"}],
    ]
    resultado = asyncio.run(s.run_full_sync([mundo.documento, otro], gestor=mundo.gestor,
                                           configuracion=mundo.config))
    assert resultado.procesados == resultado.errores == 1
    mundo.persistir.assert_called_once()
    assert mundo.persistir.call_args.args[0].drive_id == "otro"
    assert "Información reservada" not in caplog.text + str([r.__dict__ for r in caplog.records])


@pytest.mark.parametrize("modo", ["explicita", "completa"])
@pytest.mark.parametrize("misma_version", [True, False])
def test_academico_no_pasa_a_general_con_declaraciones_explicitas(mundo, modo, misma_version):
    e = mundo
    e.previo = e.previo.model_copy(update={"materia_id": 2, "periodo": "2026C2"})
    e.documento = e.documento.model_copy(update={"titulo": "GENERAL__Informacion.pdf",
        "drive_md5_checksum": ("a" if misma_version else "b") * 32})
    e.cuerpo = "Período: 2026-2C\nComisión: A\nContenido previo."
    previo = e.previo.model_dump()
    assert ejecutar(e, modo).errores == 1
    assert e.previo.model_dump() == previo
    if misma_version:
        e.leer.assert_called_once_with([91], preservar_estructura=True)
    else:
        e.leer.assert_not_called()
    e.metadata.assert_not_called()
    e.estado.assert_not_called()
    e.persistir.assert_not_called()
    e.gestor.embed_documents.assert_not_called()
    if misma_version:
        e.descargar.assert_not_called()


@pytest.mark.parametrize("modo", ["explicita", "completa"])
def test_general_real_puede_corregir_contexto_previo_sin_regenerar(mundo, modo):
    e = mundo
    e.documento = e.documento.model_copy(update={"titulo": "GENERAL__Informacion.pdf",
                                               "drive_md5_checksum": "a" * 32})
    e.cuerpo = "Orientación institucional para estudiantes, sin declaraciones de contexto."
    assert ejecutar(e, modo).errores == 0
    e.leer.assert_called_once_with([91], preservar_estructura=True)
    e.metadata.assert_called_once()
    nueva = e.metadata.call_args.args[1]
    assert nueva.materia_id is nueva.periodo is nueva.comision is None
    e.descargar.assert_not_called()
    e.gestor.embed_documents.assert_not_called()


@pytest.mark.parametrize("modo", ["explicita", "completa"])
@pytest.mark.parametrize("encabezado", ["Período: 2026-2C", "Comisión: F"])
def test_docx_encabezado_contradictorio_se_detecta_en_indexacion(mundo, modo, encabezado):
    e = mundo
    e.documento = e.documento.model_copy(update={"titulo": TITULO.replace(".pdf", ".docx"),
        "tipo_mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"})
    e.extraer.side_effect = None
    e.extraer.return_value = [{"section": encabezado, "content": "Contenido sin contexto en el cuerpo."}]
    assert ejecutar(e, modo).errores == 1
    e.persistir.assert_not_called()
    e.gestor.embed_documents.assert_not_called()


@pytest.mark.parametrize("modo", ["explicita", "completa"])
@pytest.mark.parametrize("legado_sin_encabezados", [False, True])
@pytest.mark.parametrize("nuevo_nombre", [
    "2027C1__Tecnicas-de-Programacion__COM-A__Cronograma.docx",
    "2026C2__Tecnicas-de-Programacion__COM-B__Cronograma.docx",
    "GENERAL__Informacion.docx",
])
def test_docx_rechaza_cambio_de_contexto_con_encabezados_o_legado(
    mundo, modo, nuevo_nombre, legado_sin_encabezados, caplog,
):
    e = mundo
    # DOCX real en memoria: los encabezados viven en section, no en content.
    archivo = Document()
    for encabezado in ("Período: 2026-2C", "Comisión: A"):
        archivo.add_heading(encabezado, level=1)
        archivo.add_paragraph("Contenido de la sección sin declaraciones de contexto.")
    memoria = BytesIO()
    archivo.save(memoria)
    e.descargar.return_value = memoria.getvalue()
    e.extraer.side_effect = extraer_texto_desde_bytes
    titulo_original = "2026C2__Tecnicas-de-Programacion__COM-A__Cronograma.docx"
    e.documento = e.documento.model_copy(update={"titulo": titulo_original,
        "tipo_mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"})
    e.previo = None
    assert ejecutar(e, modo).errores == 0
    fragmentos = e.persistir.call_args.args[1]
    assert "Período:" in fragmentos[0].contenido and "Comisión:" in fragmentos[1].contenido
    # Nuevos chunks conservan encabezados en contenido; el legado puede carecer de ellos.
    e.cuerpo = "\n".join(f.contenido for f in fragmentos)
    if legado_sin_encabezados:
        e.cuerpo = "\n".join(f for f in e.cuerpo.splitlines()
                            if not f.startswith(("Período:", "Comisión:")))
    e.previo = DocumentoPersistido(id=91, drive_id=e.documento.drive_id, titulo=titulo_original,
        materia_id=19, periodo="2026C2", comision="A", tipo_documental="cronograma",
        estado="activo", vigente=True, indexado_en=FECHA, drive_modified_time=FECHA,
        drive_md5_checksum=e.documento.drive_md5_checksum)
    e.documento = e.documento.model_copy(update={"titulo": nuevo_nombre})
    previo = e.previo.model_dump()
    for mock in (e.metadata, e.estado, e.persistir, e.gestor.embed_documents, e.descargar, e.extraer):
        mock.reset_mock()
    assert ejecutar(e, modo).errores == 1
    assert e.previo.model_dump() == previo
    e.metadata.assert_not_called()
    e.estado.assert_not_called()
    e.persistir.assert_not_called()
    e.descargar.assert_not_called()
    e.extraer.assert_not_called()
    e.gestor.embed_documents.assert_not_called()
    categoria = "contexto_persistido_no_verificable" if legado_sin_encabezados else "incoherencia_documental"
    assert any(getattr(r, "coherencia_documental", {}).get("categoria") == categoria
               for r in caplog.records)


@pytest.mark.parametrize("modo", ["explicita", "completa"])
def test_docx_metadata_con_contexto_explicito_reconstruible_permite_actualizar(mundo, modo):
    e = mundo
    e.documento = e.documento.model_copy(update={"titulo": TITULO.replace(".pdf", ".docx"),
        "tipo_mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "drive_md5_checksum": "a" * 32})
    e.previo = e.previo.model_copy(update={"periodo": "2026C2"})
    e.cuerpo = "Período: 2027C1\nComisión: A\nContenido previo."
    assert ejecutar(e, modo).errores == 0
    e.metadata.assert_called_once()
    e.descargar.assert_not_called()
    e.gestor.embed_documents.assert_not_called()


@pytest.mark.parametrize("modo", ["explicita", "completa"])
def test_extension_nueva_no_oculta_origen_docx_al_ampliar_alcance(mundo, modo):
    e = mundo
    e.previo = e.previo.model_copy(update={"titulo": TITULO.replace(".pdf", ".docx")})
    e.documento = e.documento.model_copy(update={"titulo": "GENERAL__Informacion.pdf",
                                               "drive_md5_checksum": "a" * 32})
    e.cuerpo = "Contenido persistido sin encabezados reconstruibles."
    assert ejecutar(e, modo).errores == 1
    e.metadata.assert_not_called()
    e.estado.assert_not_called()
    e.descargar.assert_not_called()
    e.gestor.embed_documents.assert_not_called()
