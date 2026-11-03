"""Contratos RPC FAQ y snapshots; los dobles no prueban transacciones PostgreSQL."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.db import faq as repositorio
from app.models.chat import ContextoChat
from app.models.faq import FaqCurada, FaqPendiente, FuenteFaq
from app.services.sanitizador import normalizar_comparacion
from tests.unit.services.test_cache_faq import coincidencia, evidencia


def vector():
    return [1.0] + [0.0] * 767


def base(datos):
    cliente = MagicMock()
    cliente.schema.return_value = cliente
    cliente.rpc.return_value.execute.return_value = SimpleNamespace(data=datos)
    return cliente


def curada():
    pregunta = "¿Cuándo abre la inscripción?"
    return FaqCurada(
        pregunta_canonica=pregunta, pregunta_normalizada=normalizar_comparacion(pregunta),
        respuesta="La inscripción abre el 15 de marzo.", embedding=vector(),
        confianza_evidencia=0.8,
    )


def fuente():
    return FuenteFaq(drive_id="drive-calendario", fragmento_ids=[10], es_primaria=True,
                     version_documento={"indexado_en": "2026-10-01T00:00:00"})


def test_match_transporta_vector_y_contexto_completo_normalizado():
    cliente = base([coincidencia().model_dump(mode="json")])
    resultado = repositorio.buscar_en_cache_faq(
        vector(), contexto=ContextoChat(materia_id=3, comision=" f ", periodo="2026c2"),
        tipo_documental="cronograma", umbral=0.75, umbral_evidencia=0.68, cliente=cliente,
    )
    assert resultado.id == 7
    cliente.rpc.assert_called_once_with("match_pregunta_frecuente", {
        "p_query_embedding": vector(), "p_umbral": 0.75,
        "p_materia_id": 3, "p_comision": "F", "p_periodo": "2026C2",
        "p_tipo_documental": "cronograma", "p_umbral_evidencia": 0.68,
    })


def test_match_vacio_no_es_error():
    assert repositorio.buscar_en_cache_faq(
        vector(), contexto=ContextoChat(), tipo_documental="general", umbral=0.75,
        umbral_evidencia=0.7, cliente=base([]),
    ) is None


@pytest.mark.parametrize("datos", [None, {}, [dict(id=7)], [
    coincidencia().model_dump(), coincidencia().model_dump(),
], [coincidencia().model_dump() | {"respuesta": None}]])
def test_match_invalido_es_error_explicito(datos):
    with pytest.raises(repositorio.ErrorRepositorioFaq):
        repositorio.buscar_en_cache_faq(
            vector(), contexto=ContextoChat(), tipo_documental="general", umbral=0.75,
            umbral_evidencia=0.7, cliente=base(datos),
        )


def test_snapshot_preserva_objeto_db_y_exige_todos_los_fragmentos():
    fila = evidencia().model_dump(exclude={"es_primaria"})
    cliente = base([fila])
    resultado = repositorio.obtener_fuentes_para_guardar([10], cliente=cliente)
    assert resultado[0].version_documento == fila["version_documento"]
    cliente.rpc.assert_called_once_with("obtener_fuentes_faq", {"p_fragmento_ids": [10]})
    with pytest.raises(repositorio.ErrorRepositorioFaq):
        repositorio.obtener_fuentes_para_guardar([10, 11], cliente=cliente)


def test_snapshot_rechaza_ids_repetidos_o_booleanos_antes_rpc():
    cliente = base([])
    for ids in ([10, 10], [True], []):
        with pytest.raises(ValueError):
            repositorio.obtener_fuentes_para_guardar(ids, cliente=cliente)
    cliente.rpc.assert_not_called()


def test_fuentes_de_hit_usan_rpc_all_or_nothing():
    cliente = base([evidencia().model_dump()])
    generada = coincidencia().generada_en
    assert repositorio.obtener_evidencia_faq(7, generada, cliente=cliente)[0].es_primaria
    cliente.rpc.assert_called_once_with("obtener_fuentes_cache_faq", {
        "p_pregunta_id": 7, "p_generada_en_esperada": "2026-11-01T12:00:00.123456",
    })


def test_generacion_obsoleta_es_miss_y_no_mezcla_fuentes_de_otra_respuesta():
    cliente = base([])
    assert repositorio.obtener_evidencia_faq(
        7, coincidencia().generada_en, cliente=cliente,
    ) == []


def test_fecha_limite_efimera_se_transmite_al_upsert():
    cliente = base([{"id": 7, "creada": False}])
    limite = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    faq = FaqCurada.model_validate(curada().model_dump() | {"fecha_limite": limite})
    repositorio.guardar_faq_curada(faq, [fuente()], cliente=cliente)
    assert cliente.rpc.call_args.args[1]["p_faq"]["fecha_limite"] == "2026-11-02T12:00:00Z"
    with pytest.raises(ValidationError):
        FaqCurada.model_validate(
            curada().model_dump() | {"fecha_limite": limite.replace(tzinfo=None)},
        )


def test_upsert_y_fuentes_via_una_rpc_sin_insert_http():
    cliente = base([{"id": 7, "creada": False}])
    resultado = repositorio.guardar_faq_curada(curada(), [fuente()], cliente=cliente)
    assert resultado.id == 7 and not resultado.creada
    nombre, datos = cliente.rpc.call_args.args
    assert nombre == "guardar_faq_curada"
    assert datos["p_faq"]["embedding"] == vector()
    assert datos["p_fuentes"] == [fuente().model_dump(mode="json")]
    cliente.table.assert_not_called()


def test_carga_rechaza_fuentes_ausentes_o_repetidas():
    cliente = base([])
    for fuentes in ([], [fuente(), fuente()]):
        with pytest.raises(ValueError):
            repositorio.guardar_faq_curada(curada(), fuentes, cliente=cliente)
    cliente.rpc.assert_not_called()


def test_contador_invoca_rpc_con_id_valido():
    cliente = base(None)
    repositorio.incrementar_acierto_cache_faq(7, cliente=cliente)
    cliente.rpc.assert_called_once_with("incrementar_acierto_faq", {"p_id": 7})


def test_errores_no_imprimen_detalle_del_cliente(capsys):
    cliente = base(None)
    cliente.rpc.side_effect = RuntimeError("SECRETO_PAYLOAD")
    with pytest.raises(repositorio.ErrorRepositorioFaq) as error:
        repositorio.incrementar_acierto_cache_faq(7, cliente=cliente)
    assert "SECRETO_PAYLOAD" not in str(error.value) + capsys.readouterr().out
    assert error.value.__suppress_context__


@pytest.mark.parametrize("cambios", [
    {"embedding": [1.0] * 1024}, {"embedding": [0.0] * 768},
    {"embedding": [True] * 768}, {"respuesta": "  "},
    {"pregunta_normalizada": "no corresponde"}, {"comision": "F%"},
    {"periodo": "2026"},
])
def test_modelo_carga_rechaza_contratos_invalidos(cambios):
    with pytest.raises(ValidationError):
        FaqCurada.model_validate(curada().model_dump() | cambios)


def test_pendiente_lee_vector_postgrest_sin_exponerlo_en_repr():
    pendiente = FaqPendiente(id=7, pregunta_canonica="Pregunta", embedding=str(vector()))
    assert pendiente.embedding == vector()
    assert "embedding=" not in repr(pendiente)


def test_com_general_se_persiste_null():
    faq = FaqCurada.model_validate(curada().model_dump() | {"comision": "COM-GENERAL"})
    assert faq.comision is None
