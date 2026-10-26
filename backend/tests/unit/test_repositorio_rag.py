"""Espías de PostgREST: contratos y errores, sin representar un rollback SQL real."""

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
