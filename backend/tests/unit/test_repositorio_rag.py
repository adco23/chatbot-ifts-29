"""Espías de PostgREST: contratos y errores, sin representar un rollback SQL real."""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.db import repositorio
from app.models.rag import DocumentoDrive, FiltrosRecuperacion, FragmentoParaIndexar
from app.settings import DIMENSION_EMBEDDINGS_RAG


def vector():
    return [1.0] + [0.0] * (DIMENSION_EMBEDDINGS_RAG - 1)


def base_falsa(datos):
    base = MagicMock()
    base.schema.return_value = base
    base.rpc.return_value.execute.return_value = SimpleNamespace(data=datos)
    return base


@pytest.mark.parametrize("preservar,esperado", [
    (False, "Comisión: F Período: 2026-2C Segundo fragmento"),
    (True, "Comisión: F\nPeríodo: 2026-2C\nSegundo fragmento"),
])
def test_lectura_opcional_conserva_encabezados_sin_cambiar_normalizacion_actual(preservar, esperado):
    base = base_falsa([])
    consulta = base.table.return_value
    for metodo in ("select", "in_", "gt", "order", "limit"):
        getattr(consulta, metodo).return_value = consulta
    consulta.execute.side_effect = [SimpleNamespace(data=[
        {"id": 1, "documento_id": 91, "orden": 2, "contenido": "Segundo fragmento"},
        {"id": 2, "documento_id": 91, "orden": 1, "contenido": "Comisión: F\nPeríodo: 2026-2C"},
    ]), SimpleNamespace(data=[])]
    opciones = {"preservar_estructura": True} if preservar else {}
    assert repositorio.obtener_contenidos_documentos([91], cliente=base, **opciones) == {91: esperado}
    base.rpc.assert_not_called()


def fila_fragmento():
    return {
        "fragmento_id": 10, "documento_id": 1, "contenido": "Inscripción",
        "orden": 1, "similitud": 0.75, "titulo": "FAQ", "drive_id": "drive-faq",
        "materia_id": None, "comision": None, "periodo": None, "vigente": True,
    }


def test_busqueda_envia_filtros_y_mapea_resultados():
    base = base_falsa([fila_fragmento()])
    filtros = FiltrosRecuperacion(materia_id=2, comision=" A ", periodo="2026", vigente=None)
    encontrados = repositorio.buscar_fragmentos_similares(
        vector(), filtros=filtros, top_k=3, documento_ids=[1], cliente=base
    )
    nombre, parametros = base.rpc.call_args.args
    assert nombre == "match_fragmentos"
    assert parametros == {
        "p_query_embedding": vector(), "p_top_k": 3, "p_materia_id": 2,
        "p_comision": "A", "p_periodo": "2026", "p_vigente": None,
        "p_documento_ids": [1],
    }
    assert encontrados[0].similitud == 0.75
    assert encontrados[0].contenido == "Inscripción"
    assert encontrados[0].drive_id == "drive-faq"


def test_sin_coincidencias_no_es_error_y_preserva_ids_vacios():
    base = base_falsa([])
    assert repositorio.buscar_fragmentos_similares(
        vector(), top_k=5, documento_ids=[], cliente=base
    ) == []
    parametros = base.rpc.call_args.args[1]
    assert parametros["p_materia_id"] is None
    assert parametros["p_vigente"] is True
    assert parametros["p_documento_ids"] == []


def test_reemplazo_es_una_rpc_sin_delete_insert_http():
    instante = datetime(2026, 10, 23, 12)
    base = base_falsa([{
        "documento_id": 7, "fragmentos_indexados": 2, "indexado_en": instante.isoformat()
    }])
    documento = DocumentoDrive(drive_id="drive-faq", titulo="FAQ", tipo_mime="application/pdf")
    fragmentos = [
        FragmentoParaIndexar(contenido="Primero", vector=vector(), orden=1),
        FragmentoParaIndexar(contenido="Segundo", vector=vector(), orden=2),
    ]
    resumen = repositorio.reemplazar_fragmentos_documento(documento, fragmentos, cliente=base)
    assert resumen.documento_id == 7
    assert resumen.indexado_en == instante
    base.rpc.assert_called_once_with("reemplazar_fragmentos_documento_versionado", {
        "p_drive_id": "drive-faq", "p_titulo": "FAQ",
        "p_fragmentos": [item.model_dump() for item in fragmentos],
        "p_drive_modified_time": None, "p_drive_md5_checksum": None, "p_propietario": None,
    })
    base.table.assert_not_called()


def test_rechaza_lote_desordenado_antes_de_persistir():
    base = base_falsa([])
    documento = DocumentoDrive(drive_id="drive", titulo="FAQ", tipo_mime="application/pdf")
    with pytest.raises(ValueError):
        repositorio.reemplazar_fragmentos_documento(
            documento, [FragmentoParaIndexar(contenido="Uno", vector=vector(), orden=2)],
            cliente=base,
        )
    base.rpc.assert_not_called()


def test_error_rpc_no_se_convierte_en_lista_vacia_ni_filtra_payload():
    base = base_falsa([])
    base.rpc.side_effect = RuntimeError("contenido privado no imprimible")
    with pytest.raises(repositorio.ErrorRepositorio) as capturado:
        repositorio.buscar_fragmentos_similares(vector(), top_k=5, cliente=base)
    assert "privado" not in str(capturado.value)
    assert capturado.value.__suppress_context__ is True


@pytest.mark.parametrize("datos", [None, {}, [{"contenido": "incompleto"}]])
def test_respuestas_mal_formadas_son_errores(datos):
    with pytest.raises(repositorio.ErrorRepositorio):
        repositorio.buscar_fragmentos_similares(vector(), top_k=5, cliente=base_falsa(datos))


def test_base_ausente_no_es_corpus_vacio(monkeypatch):
    monkeypatch.setattr(repositorio, "obtener_cliente_supabase", lambda: None)
    with pytest.raises(repositorio.ErrorRepositorio):
        repositorio.buscar_fragmentos_similares(vector(), top_k=5)


def test_documento_inexistente_y_documento_tipado():
    base = base_falsa([])
    consulta = base.table.return_value.select.return_value.eq.return_value.limit.return_value
    consulta.execute.return_value = SimpleNamespace(data=[])
    assert repositorio.obtener_documento_por_drive_id("drive", cliente=base) is None
    consulta.execute.return_value = SimpleNamespace(data=[{
        "id": 1, "titulo": "FAQ", "drive_id": "drive", "materia_id": None,
        "comision": None, "periodo": None, "vigente": True,
        "estado": "activo", "indexado_en": None,
    }])
    assert repositorio.obtener_documento_por_drive_id("drive", cliente=base).id == 1


def test_helpers_acotan_documento_y_timestamp_utc():
    base = base_falsa([])
    base.table.return_value.delete.return_value.eq.return_value.execute.return_value = (
        SimpleNamespace(data=[])
    )
    repositorio.eliminar_fragmentos_documento(7, cliente=base)
    base.table.return_value.delete.return_value.eq.assert_called_once_with("documento_id", 7)
    base.table.return_value.update.return_value.eq.return_value.execute.return_value = (
        SimpleNamespace(data=[{"id": 7}])
    )
    repositorio.actualizar_documento_indexado(7, cliente=base)
    parametros = base.table.return_value.update.call_args.args[0]
    assert datetime.fromisoformat(parametros["indexado_en"]).tzinfo is None
    base.table.return_value.update.return_value.eq.assert_called_once_with("id", 7)


def test_rechaza_vector_incompatible_sin_llamar_rpc():
    base = base_falsa([])
    with pytest.raises(ValueError):
        repositorio.buscar_fragmentos_similares([1.0] * 1024, top_k=5, cliente=base)
    base.rpc.assert_not_called()


def fila_persistida(identidad=1, orden=1, **cambios):
    return {"id": identidad, "documento_id": 64, "contenido": "PFO 2: del 21 al 27 de septiembre.",
            "vector": vector(), "orden": orden} | cambios


def base_fragmentos(*paginas):
    base = base_falsa([])
    consulta = base.table.return_value
    for metodo in ("select", "eq", "order", "range"):
        getattr(consulta, metodo).return_value = consulta
    consulta.execute.side_effect = [SimpleNamespace(data=filas) for filas in paginas]
    return base, consulta


@pytest.mark.parametrize("como_json", [False, True])
def test_fragmentos_completos_normalizan_vector_y_solo_leen_documento(como_json):
    fila = fila_persistida(vector=json.dumps(vector()) if como_json else vector())
    base, consulta = base_fragmentos([fila], [])
    encontrados = repositorio.obtener_fragmentos_documento(64, cliente=base)
    assert len(encontrados) == 1
    assert encontrados[0].id == 1 and encontrados[0].documento_id == 64
    assert encontrados[0].vector == vector() and encontrados[0].orden == 1
    assert encontrados[0].contenido == fila["contenido"]
    assert all(llamada.args == ("public",) for llamada in base.schema.call_args_list)
    assert all(llamada.args == ("fragmento",) for llamada in base.table.call_args_list)
    assert all(llamada.args == ("documento_id", 64) for llamada in consulta.eq.call_args_list)
    assert consulta.order.call_args_list[0].args == ("orden",)
    assert consulta.order.call_args_list[1].args == ("id",)
    assert consulta.select.call_args.args == ("id,documento_id,contenido,vector,orden",)
    base.rpc.assert_not_called()
    for metodo in ("insert", "update", "delete", "upsert"):
        getattr(consulta, metodo).assert_not_called()


def test_fragmentos_paginados_no_truncan_respaldo(monkeypatch):
    monkeypatch.setattr(repositorio, "_TAMANIO_PAGINA_FRAGMENTOS", 2)
    base, consulta = base_fragmentos(
        [fila_persistida(10, 1), fila_persistida(5, 2)], [fila_persistida(7, 3)], [],
    )
    encontrados = repositorio.obtener_fragmentos_documento(64, cliente=base)
    assert [f.id for f in encontrados] == [10, 5, 7]
    assert [f.orden for f in encontrados] == [1, 2, 3]
    assert [llamada.args for llamada in consulta.range.call_args_list] == [(0, 1), (2, 3), (3, 4)]


def test_fragmentos_continuan_tras_pagina_corta_del_servidor(monkeypatch):
    monkeypatch.setattr(repositorio, "_TAMANIO_PAGINA_FRAGMENTOS", 2)
    base, consulta = base_fragmentos([fila_persistida()], [fila_persistida(2, 2)], [])
    assert len(repositorio.obtener_fragmentos_documento(64, cliente=base)) == 2
    assert [llamada.args for llamada in consulta.range.call_args_list] == [(0, 1), (1, 2), (2, 3)]


def test_fragmentos_ausentes_son_lista_vacia():
    base, _ = base_fragmentos([])
    assert repositorio.obtener_fragmentos_documento(64, cliente=base) == []


@pytest.mark.parametrize("cambios", [
    {"documento_id": 65}, {"id": 0}, {"id": True}, {"id": "1"},
    {"documento_id": True}, {"documento_id": "64"},
    {"orden": 0}, {"orden": 2}, {"orden": True}, {"orden": "1"},
    {"contenido": "  "}, {"contenido": None}, {"contenido": 1},
    {"vector": None}, {"vector": "true"}, {"vector": "[malformado]"},
    {"vector": [1.] * 767}, {"vector": [1.] * 1024}, {"vector": [0.] * 768},
    {"vector": [True] + [0.] * 767}, {"vector": '[true]' },
    {"vector": [float("nan")] + [0.] * 767},
    {"vector": [float("inf")] + [0.] * 767},
    {"vector": ["1"] + [0.] * 767},
    {"vector": json.dumps([True] + [0.] * 767)},
    {"vector": json.dumps([float("inf")] + [0.] * 767)},
])
def test_fragmentos_invalidos_no_son_un_respaldo_utilizable(cambios):
    base, _ = base_fragmentos([fila_persistida(**cambios)])
    with pytest.raises(repositorio.ErrorRepositorio, match="contrato RAG"):
        repositorio.obtener_fragmentos_documento(64, cliente=base)


@pytest.mark.parametrize("campo", ["id", "documento_id", "contenido", "vector", "orden"])
def test_fragmentos_incompletos_se_rechazan(campo):
    fila = fila_persistida()
    del fila[campo]
    base, _ = base_fragmentos([fila])
    with pytest.raises(repositorio.ErrorRepositorio):
        repositorio.obtener_fragmentos_documento(64, cliente=base)


@pytest.mark.parametrize("segunda", [fila_persistida(1, 2), fila_persistida(2, 1), fila_persistida(2, 3)])
def test_ids_ordenes_repetidos_o_saltos_se_rechazan_entre_paginas(segunda):
    base, _ = base_fragmentos([fila_persistida()], [segunda])
    with pytest.raises(repositorio.ErrorRepositorio):
        repositorio.obtener_fragmentos_documento(64, cliente=base)


@pytest.mark.parametrize("valor", [0, -1, True, "64"])
def test_lectura_fragmentos_valida_id_antes_de_acceder(valor):
    base, _ = base_fragmentos([])
    with pytest.raises(ValueError):
        repositorio.obtener_fragmentos_documento(valor, cliente=base)
    base.schema.assert_not_called()


def test_fallo_remoto_no_se_convierte_en_respaldo_vacio_ni_filtra_payload(caplog):
    base, consulta = base_fragmentos([])
    consulta.execute.side_effect = RuntimeError("contenido privado del servidor")
    with pytest.raises(repositorio.ErrorRepositorio) as capturado:
        repositorio.obtener_fragmentos_documento(64, cliente=base)
    assert "privado" not in str(capturado.value) and "privado" not in caplog.text
    assert capturado.value.__suppress_context__ is True


@pytest.mark.parametrize("datos", [None, {}, [None]])
def test_respuesta_fragmentos_no_tabular_se_rechaza(datos):
    base, _ = base_fragmentos(datos)
    with pytest.raises(repositorio.ErrorRepositorio):
        repositorio.obtener_fragmentos_documento(64, cliente=base)
