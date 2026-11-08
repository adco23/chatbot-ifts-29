"""Contratos RPC de metadata: no equivalen a una prueba de atomicidad PostgreSQL."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.db import repositorio as r
from app.models.rag import DocumentoDrive, FiltrosRecuperacion, FragmentoParaIndexar, MetadataDocumental


def cliente(filas):
    base = MagicMock()
    base.schema.return_value = base
    base.rpc.return_value.execute.return_value = SimpleNamespace(data=filas)
    return base


def documento():
    return DocumentoDrive(drive_id="misma-identidad", titulo="GENERAL__Ingreso.pdf",
                          tipo_mime="application/pdf", drive_modified_time=datetime(2026, 10, 25, tzinfo=UTC))


def test_reemplazo_academico_una_rpc_y_nulls_explicitos():
    fecha = datetime(2026, 10, 25)
    base = cliente([{"documento_id": 7, "fragmentos_indexados": 1, "indexado_en": fecha}])
    fragmento = FragmentoParaIndexar(contenido="Ingreso", vector=[1.0] + [0.0] * 767, orden=1)
    r.reemplazar_fragmentos_documento(documento(), [fragmento], metadata=MetadataDocumental(tipo="General"),
                                     ejecucion_id="propietario", cliente=base)
    base.rpc.assert_called_once()
    nombre, params = base.rpc.call_args.args
    assert nombre == "reemplazar_fragmentos_documento_tipado"
    assert params["p_tipo_documental"] == "general"
    assert params["p_materia_id"] is params["p_comision"] is params["p_periodo"] is None
    assert params["p_propietario"] == "propietario"
    base.table.assert_not_called()


def test_metadata_sin_fragmentos_y_materia_resuelta():
    base = cliente([{"documento_id": 7, "cambiado": True}])
    metadata = MetadataDocumental(tipo="Programa", materia_id=91, comision=None, periodo="2026C2",
                                  materia_nombre="Nombre oficial")
    assert r.actualizar_metadata_documento_sync(documento(), metadata, "propietario", cliente=base)
    nombre, params = base.rpc.call_args.args
    assert nombre == "actualizar_metadata_documento_tipado"
    assert params["p_tipo_documental"] == "programa"
    assert params["p_materia_id"] == 91 and params["p_comision"] is None
    assert params["p_drive_id"] == "misma-identidad"
    assert "p_fragmentos" not in params and "p_indexado_en" not in params
    base.table.assert_not_called()


@pytest.mark.parametrize("filas", [[], [{"cambiado": "true"}], [{"cambiado": None}]])
def test_respuesta_rpc_invalida(filas):
    with pytest.raises(r.ErrorRepositorio):
        r.actualizar_metadata_documento_sync(documento(), MetadataDocumental(tipo="General"),
                                             None, cliente=cliente(filas))


def test_fallo_rpc_sin_payload():
    base = cliente([])
    base.rpc.side_effect = RuntimeError("SECRETO")
    with pytest.raises(r.ErrorRepositorio) as error:
        r.actualizar_metadata_documento_sync(documento(), MetadataDocumental(tipo="General"), None, cliente=base)
    assert "SECRETO" not in str(error.value)


def test_catalogo_completo_paginado():
    base = MagicMock()
    consulta = base.table.return_value
    for metodo in ("select", "gt", "order", "limit"):
        getattr(consulta, metodo).return_value = consulta
    filas = [{"id": i, "codigo": str(i), "nombre": f"Materia {i}"} for i in range(1, 1202)]
    consulta.execute.side_effect = [SimpleNamespace(data=f) for f in
                                    [filas[:500], filas[500:1000], filas[1000:], []]]
    assert len(r.listar_materias_chat(cliente=base)) == 1201
    assert [c.args[1] for c in consulta.gt.call_args_list] == [0, 500, 1000, 1201]


def test_catalogo_rechaza_paginacion_repetida():
    base = MagicMock()
    consulta = base.table.return_value
    for metodo in ("select", "gt", "order", "limit"):
        getattr(consulta, metodo).return_value = consulta
    consulta.execute.return_value = SimpleNamespace(data=[{"id": 3, "codigo": "X", "nombre": "X"}])
    with pytest.raises(r.ErrorRepositorio):
        r.listar_materias_chat(cliente=base)


@pytest.mark.parametrize("modalidad", ["primaria", "comun", "complementaria"])
def test_nueva_rpc_academica_transporta_tipo_periodo_y_modalidad(modalidad):
    base = cliente([])
    filtros = FiltrosRecuperacion(materia_id=91, comision="F", periodo="2026C2", tipo_documental="cronograma")
    assert r.buscar_fragmentos_similares([1.0] + [0.0] * 767, top_k=5, filtros=filtros,
                                         modalidad=modalidad, cliente=base) == []
    nombre, params = base.rpc.call_args.args
    assert nombre == "match_fragmentos_academicos"
    assert params["p_tipo_documental"] == "cronograma" and params["p_modalidad"] == modalidad
    assert params["p_materia_id"] == 91 and params["p_comision"] == "F"
    assert params["p_periodo"] == "2026C2" and params["p_vigente"] is True


def test_rpc_generica_conserva_parametros_anteriores():
    base = cliente([])
    r.buscar_fragmentos_similares([1.0] + [0.0] * 767, top_k=5, cliente=base)
    nombre, params = base.rpc.call_args.args
    assert nombre == "match_fragmentos"
    assert set(params) == {"p_query_embedding", "p_top_k", "p_materia_id", "p_comision", "p_periodo",
                           "p_vigente", "p_documento_ids"}


def test_firmas_comparan_texto_completo_ordenado_sin_vectores():
    base = cliente([])
    consulta = base.table.return_value
    for metodo in ("select", "in_", "gt", "order", "limit"):
        getattr(consulta, metodo).return_value = consulta
    consulta.execute.side_effect = [SimpleNamespace(data=[
        {"id": 1, "documento_id": 7, "orden": 2, "contenido": "Unidad dos"},
        {"id": 2, "documento_id": 8, "orden": 1, "contenido": "Unidad   uno"},
        {"id": 3, "documento_id": 7, "orden": 1, "contenido": "Unidad uno"},
        {"id": 4, "documento_id": 8, "orden": 2, "contenido": "Unidad dos"},
        {"id": 5, "documento_id": 9, "orden": 1, "contenido": "Otro programa"},
    ]), SimpleNamespace(data=[])]
    firmas = r.obtener_firmas_contenido_documentos([7, 8, 9, 10], cliente=base)
    assert firmas[7] == firmas[8] and firmas[7] != firmas[9]
    assert 10 not in firmas  # No equiparar documentos vacíos.
    consulta.select.assert_called_with("id,documento_id,orden,contenido")
    base.rpc.assert_not_called()


def test_fallo_comparacion_no_se_interpreta_como_programas_iguales():
    base = cliente([])
    base.table.side_effect = RuntimeError("SECRETO")
    with pytest.raises(r.ErrorRepositorio) as error:
        r.obtener_firmas_contenido_documentos([7, 8], cliente=base)
    assert "SECRETO" not in str(error.value)
