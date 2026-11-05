"""T42/T44: HIT documentado, MISS seguro y métricas sin servicios externos."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.models.chat import ContextoChat, FuenteChat
from app.models.faq import CoincidenciaFaq, FragmentoFuenteFaq
from app.providers.embeddings.base import ResultadoEmbedding
from app.services import cache_faq
from app.services.validacion_embeddings_rag import ErrorEmbeddingIncompatible


def coincidencia(**cambios):
    return CoincidenciaFaq(**(dict(
        id=7, pregunta_canonica="¿Cuándo abre la inscripción?",
        respuesta="La inscripción abre el 15 de marzo.", materia_id=None,
        comision=None, periodo=None, tipo_documental="general", estado="activa",
        vigencia_hasta=None, similitud=0.91, confianza_evidencia=0.8,
        generada_en="2026-11-01T12:00:00.123456",
    ) | cambios))


def evidencia(**cambios):
    return FragmentoFuenteFaq(**(dict(
        fragmento_id=10, documento_id=20, contenido="La inscripción abre el 15 de marzo.",
        orden=1, titulo="Calendario institucional", drive_id="drive-calendario",
        materia_id=None, comision=None, periodo=None, vigente=True,
        tipo_documental="general", estado_documento="activo",
        version_documento={"indexado_en": "2026-10-01T00:00:00"}, es_primaria=True,
    ) | cambios))


@pytest.fixture
def entorno(monkeypatch):
    config = SimpleNamespace(
        embedding_primary="gemini", gemini_embedding_modelo="gemini-embedding-001",
        gemini_embedding_dimension=768, umbral_cache_faq=0.75,
        umbral_rag_para_tipo=lambda tipo: 0.7,
    )
    buscar = Mock(return_value=coincidencia())
    fuentes = Mock(return_value=[evidencia()])
    incrementar = Mock()
    resolver = AsyncMock(return_value=[FuenteChat(
        documento_id=20, drive_id="drive-calendario", titulo="Calendario institucional",
        enlace=None, fragmento_ids=[10],
    )])
    monkeypatch.setattr(cache_faq.repositorio, "buscar_en_cache_faq", buscar)
    monkeypatch.setattr(cache_faq.repositorio, "obtener_evidencia_faq", fuentes)
    monkeypatch.setattr(cache_faq.repositorio, "incrementar_acierto_cache_faq", incrementar)
    return SimpleNamespace(config=config, buscar=buscar, fuentes=fuentes,
                           incrementar=incrementar, resolver=resolver)


def ejecutar(entorno, *, contexto=None, tipo="general", embedding=None):
    return asyncio.run(cache_faq.buscar_faq(
        "¿Cuándo abre la inscripción?", contexto=contexto or ContextoChat(),
        tipo_documental=tipo, configuracion=entorno.config,
        embedding=embedding or ResultadoEmbedding(
            [1.0] + [0.0] * 767, "gemini", "gemini-embedding-001", 768),
        resolver_fuentes=entorno.resolver,
    ))


def test_hit_general_tiene_fuentes_advertencia_y_un_lookup(entorno):
    resultado = ejecutar(entorno, contexto=ContextoChat(consulta_pendiente="Consulta previa"))
    assert resultado.estado == "respondida"
    assert resultado.fuentes[0].documento_id == 20
    assert resultado.fuentes[0].enlace is None
    assert resultado.confianza == 0.91 and resultado.umbral == 0.75
    assert resultado.advertencia_temporal
    assert resultado.contexto.consulta_pendiente is None
    entorno.buscar.assert_called_once()
    entorno.fuentes.assert_called_once_with(7, coincidencia().generada_en)
    entorno.incrementar.assert_called_once_with(7)


def test_miss_no_consulta_fuentes_ni_contador(entorno):
    entorno.buscar.return_value = None
    assert ejecutar(entorno) is None
    entorno.fuentes.assert_not_called()
    entorno.incrementar.assert_not_called()


@pytest.mark.parametrize("cambios", [
    {"estado": "pendiente"}, {"estado": "inactiva"},
    {"vigencia_hasta": "2000-01-01"}, {"similitud": 0.749},
    {"confianza_evidencia": 0.699}, {"materia_id": 3}, {"comision": "F"},
    {"periodo": "2026C2"}, {"tipo_documental": "cronograma"},
])
def test_no_acepta_faq_ineligible_sin_contexto(entorno, cambios):
    entorno.buscar.return_value = coincidencia(**cambios)
    assert ejecutar(entorno) is None
    entorno.incrementar.assert_not_called()


@pytest.mark.parametrize("filas", [
    [], [evidencia(estado_documento="inactivo")], [evidencia(vigente=False)],
    [evidencia(vigente=None)], [evidencia(materia_id=3)], [evidencia(comision="F")],
    [evidencia(periodo="2026C2")],
    [evidencia(contenido="La inscripción abre el 16 de marzo.")],
])
def test_fuentes_ausentes_inactivas_o_no_respaldadas_son_miss(entorno, filas):
    entorno.fuentes.return_value = filas
    assert ejecutar(entorno) is None
    entorno.incrementar.assert_not_called()


def test_no_acepta_fuentes_construidas_incompletas(entorno):
    entorno.resolver.return_value[0].fragmento_ids = []
    assert ejecutar(entorno) is None
    entorno.incrementar.assert_not_called()


def preparar_academica(entorno, *, tipo="cronograma", comision="F", periodo="2026C2"):
    entorno.buscar.return_value = coincidencia(
        materia_id=3, comision=comision, periodo=periodo, tipo_documental=tipo)
    entorno.fuentes.return_value = [evidencia(
        materia_id=3, comision=comision, periodo=periodo, tipo_documental=tipo)]
    return ContextoChat(materia_id=3, comision=comision, periodo=periodo)


def test_hit_materia_comision_periodo_exactos_y_evidencia_primaria(entorno):
    contexto = preparar_academica(entorno)
    respuesta = ejecutar(entorno, contexto=contexto, tipo="cronograma")
    assert respuesta.estado == "respondida"
    parametros = entorno.buscar.call_args.kwargs
    assert parametros["contexto"] == contexto
    assert parametros["tipo_documental"] == "cronograma"
    assert parametros["umbral_evidencia"] == 0.7


@pytest.mark.parametrize("campo,valor", [
    ("materia_id", 99), ("comision", "A"), ("periodo", "2026C1"),
    ("tipo_documental", "programa"),
])
def test_rechaza_contexto_distinto_y_programa_no_responde_horario(entorno, campo, valor):
    contexto = preparar_academica(entorno)
    entorno.buscar.return_value = entorno.buscar.return_value.model_copy(update={campo: valor})
    assert ejecutar(entorno, contexto=contexto, tipo="cronograma") is None
    entorno.incrementar.assert_not_called()


def test_solo_complemento_no_habilita_hit_academico(entorno):
    contexto = preparar_academica(entorno)
    entorno.fuentes.return_value[0] = entorno.fuentes.return_value[0].model_copy(
        update={"es_primaria": False})
    assert ejecutar(entorno, contexto=contexto, tipo="cronograma") is None
    entorno.incrementar.assert_not_called()


def test_programa_comun_aplicable_sin_inventar_comision(entorno):
    contexto = preparar_academica(entorno, tipo="programa", comision=None)
    resultado = ejecutar(entorno, contexto=contexto, tipo="programa")
    assert resultado.estado == "respondida" and resultado.contexto.comision is None


@pytest.mark.parametrize("proveedor,modelo,dimension,longitud", [
    ("cloudflare", "otro", 768, 768), ("gemini", "otro", 768, 768),
    ("gemini", "gemini-embedding-001", 1024, 1024),
    ("gemini", "gemini-embedding-001", 768, 767),
])
def test_embedding_incorrecto_rechazado_antes_del_lookup(
    entorno, proveedor, modelo, dimension, longitud,
):
    with pytest.raises(ErrorEmbeddingIncompatible):
        ejecutar(entorno, embedding=ResultadoEmbedding([1.0] * longitud,
                                                       proveedor, modelo, dimension))
    entorno.buscar.assert_not_called()


def test_contador_solo_despues_de_fuentes_y_respuesta_valida(entorno):
    eventos = []

    async def resolver(fragmentos):
        eventos.append("fuentes_validadas")
        return [FuenteChat(documento_id=20, drive_id="drive-calendario", titulo="Calendario",
                           fragmento_ids=[10])]

    entorno.resolver.side_effect = resolver
    entorno.incrementar.side_effect = lambda identidad: eventos.append("contador")
    assert ejecutar(entorno).estado == "respondida"
    assert eventos == ["fuentes_validadas", "contador"]


def test_fallo_contador_no_rompe_respuesta_ni_expone_error(entorno, caplog, capsys):
    entorno.incrementar.side_effect = RuntimeError("SECRETO_PAYLOAD")
    assert ejecutar(entorno).estado == "respondida"
    assert "faq_acierto_no_registrado" in caplog.text
    assert "SECRETO_PAYLOAD" not in caplog.text + capsys.readouterr().out


def test_error_rpc_es_miss_seguro_sin_payload(entorno, caplog, capsys):
    entorno.buscar.side_effect = RuntimeError("SECRETO_PAYLOAD")
    assert ejecutar(entorno) is None
    assert "SECRETO_PAYLOAD" not in caplog.text + capsys.readouterr().out
    entorno.incrementar.assert_not_called()
