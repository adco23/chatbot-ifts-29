"""Contratos PostgREST incrementales y verificación CLI; no prueban atomicidad SQL."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from app.db import repositorio as r
from app.models.rag import DocumentoDrive, DocumentoPersistido, FragmentoParaIndexar


def cliente(filas):
    base = MagicMock()
    base.schema.return_value = base
    consulta = base.table.return_value
    for metodo in ("select", "gt", "order", "limit", "eq"):
        getattr(consulta, metodo).return_value = consulta
    consulta.execute.return_value = SimpleNamespace(data=filas, count=len(filas))
    base.rpc.return_value.execute.return_value = SimpleNamespace(data=filas)
    return base


def test_lee_todas_las_paginas_incluyendo_inactivos():
    base = cliente([])
    filas = [{"id": i, "titulo": "FAQ", "drive_id": str(i), "estado": "inactivo"}
             for i in range(1, 1202)]
    base.table.return_value.execute.side_effect = [
        SimpleNamespace(data=filas[:500]), SimpleNamespace(data=filas[500:1000]),
        SimpleNamespace(data=filas[1000:]), SimpleNamespace(data=[]),
    ]
    assert len(r.listar_documentos_sync(cliente=base)) == 1201
    assert [c.args[1] for c in base.table.return_value.gt.call_args_list] == [0, 500, 1000, 1201]
    base.table.return_value.eq.assert_not_called()


def test_paginacion_invalida_falla_sin_bucle():
    base = cliente([{"id": 0, "titulo": "FAQ", "drive_id": "abc"}])
    with pytest.raises(r.ErrorRepositorio):
        r.listar_documentos_sync(cliente=base)


@pytest.mark.parametrize("accion", ["adquirir", "renovar", "liberar"])
def test_contrato_bloqueo(accion):
    base = cliente([{"adquirido": True}])
    assert r.gestionar_bloqueo_sync("propietario", accion, cliente=base)
    base.rpc.assert_called_once_with("gestionar_bloqueo_sync", {
        "p_propietario": "propietario", "p_accion": accion,
    })


def test_estado_se_compara_por_drive_id_no_nombre_y_no_borra():
    base = cliente([{"documento_id": 7, "cambiado": True}])
    assert r.actualizar_estado_documento_sync("drive-7", False, "propietario", cliente=base)
    base.rpc.assert_called_once_with("actualizar_estado_documento_sync", {
        "p_drive_id": "drive-7", "p_activo": False, "p_propietario": "propietario",
        "p_titulo": None,
    })
    base.table.assert_not_called()


def test_reemplazo_versionado_es_una_rpc():
    fecha = datetime(2026, 10, 23, tzinfo=UTC)
    base = cliente([{"documento_id": 7, "fragmentos_indexados": 1, "indexado_en": fecha}])
    documento = DocumentoDrive(drive_id="abc", titulo="FAQ", tipo_mime="application/pdf",
                               drive_modified_time=fecha, drive_md5_checksum="a" * 32)
    fragmento = FragmentoParaIndexar(contenido="Texto", vector=[1.0] + [0.0] * 767, orden=1)
    r.reemplazar_fragmentos_documento(documento, [fragmento], cliente=base, ejecucion_id="dueño")
    nombre, parametros = base.rpc.call_args.args
    assert nombre == "reemplazar_fragmentos_documento_versionado"
    assert parametros["p_drive_modified_time"] == fecha.isoformat()
    assert parametros["p_drive_md5_checksum"] == "a" * 32
    assert parametros["p_propietario"] == "dueño"
    base.rpc.assert_called_once()
    base.table.assert_not_called()


@pytest.mark.parametrize("filas", [[], [{"adquirido": "true"}], [{"adquirido": None}]])
def test_respuesta_bloqueo_invalida(filas):
    with pytest.raises(r.ErrorRepositorio):
        r.gestionar_bloqueo_sync("dueño", "adquirir", cliente=cliente(filas))


def test_conteo_no_descarga_fragmentos():
    base = cliente([{}, {}])
    assert r.contar_fragmentos_documento(7, cliente=base) == 2
    base.table.return_value.select.assert_called_once_with("id", count="exact", head=True)


def test_cli_verificar_solo_lee_y_no_genera_llm(monkeypatch, capsys):
    from app.services import recuperador, sincronizador
    from app.providers.embeddings import dependencias
    from scripts import sincronizar_drive as cli

    documentos = [DocumentoPersistido(id=i, drive_id=str(i), titulo="Prueba", estado="inactivo",
                                     vigente=False) for i in [7, 8]]
    monkeypatch.setattr(r, "listar_documentos_sync", Mock(return_value=documentos))
    monkeypatch.setattr(r, "contar_fragmentos_documento", Mock(return_value=2))
    recuperar = AsyncMock(return_value=SimpleNamespace(fragmentos=[]))
    monkeypatch.setattr(recuperador, "recuperar_fragmentos", recuperar)
    sync = AsyncMock()
    monkeypatch.setattr(sincronizador, "sincronizar_drive_completo", sync)
    monkeypatch.setattr(dependencias, "obtener_gestor_embeddings",
                        Mock(return_value=SimpleNamespace(proveedores=[])))
    args = cli.crear_analizador().parse_args([
        "verificar", "--documento-id", "7", "--documento-id", "8", "--consulta", "DELTA2026",
    ])
    assert asyncio.run(cli._ejecutar(args)) == 0
    assert '"verificacion_correcta": true' in capsys.readouterr().out
    assert recuperar.call_args.kwargs["documento_ids"] == [7, 8]
    sync.assert_not_called()


def test_cli_validar_nombres_solo_lectura_y_diagnostico_exacto(monkeypatch, capsys):
    import json
    from app.drive import cliente as drive
    from app.providers.embeddings import dependencias
    from app.services import sincronizador
    from scripts import sincronizar_drive as cli

    documentos = [
        DocumentoDrive(drive_id="general", titulo="GENERAL__Ingreso.pdf", tipo_mime="application/pdf"),
        DocumentoDrive(drive_id="especifico", titulo="2026C2__Materia-real__COM-F__Cronograma.pdf",
                       tipo_mime="application/pdf"),
        DocumentoDrive(drive_id="invalidado", titulo="2026C2__Inexistente__COM-A__Programa.pdf",
                       tipo_mime="application/pdf"),
    ]
    monkeypatch.setattr(drive, "listar_documentos_sincronizables", Mock(return_value=documentos))
    monkeypatch.setattr(r, "listar_materias_chat", Mock(return_value=[SimpleNamespace(id=91, nombre="Materia real")]))
    prohibidos = []
    for modulo, nombre in [
        (drive, "descargar_contenido_archivo"), (r, "gestionar_bloqueo_sync"),
        (r, "actualizar_metadata_documento_sync"), (r, "actualizar_estado_documento_sync"),
        (r, "reemplazar_fragmentos_documento"), (dependencias, "obtener_gestor_embeddings"),
        (sincronizador, "sincronizar_drive_completo"),
    ]:
        doble = Mock(side_effect=AssertionError("Operación prohibida en preflight"))
        monkeypatch.setattr(modulo, nombre, doble)
        prohibidos.append(doble)
    from app.providers import dependencias as llm
    llm_getter = Mock(side_effect=AssertionError("No usar LLM"))
    monkeypatch.setattr(llm, "obtener_gestor_proveedores", llm_getter)
    args = cli.crear_analizador().parse_args(["validar-nombres"])
    assert asyncio.run(cli._ejecutar(args)) == 1
    salida = json.loads(capsys.readouterr().out)
    assert salida["documentos"][0]["metadata"]["materia_id"] is None
    assert salida["documentos"][1]["metadata"]["materia_id"] == 91
    assert salida["requieren_correccion"] == [{
        "drive_id": "invalidado", "nombre": documentos[2].titulo, "error": "materia_no_reconocida",
    }]
    for doble in prohibidos + [llm_getter]:
        doble.assert_not_called()
